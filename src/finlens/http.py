"""One shared HTTP client for every outbound request.

All fetching goes through HttpService so the rules can't be skipped:
robots.txt is checked, each host is rate-limited, transient failures are retried,
and large files stream to disk instead of memory.

    async with HttpService() as http:
        resp = await http.get("https://www.nseindia.com/api/annual-reports?...")
        sha256, size = await http.download(pdf_url, Path("data/x.pdf"))
"""

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from finlens.config import settings

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    """robots.txt forbids this URL. Never retried and never worked around."""


class HttpService:
    def __init__(
        self,
        *,
        min_interval: float | None = None,
        max_attempts: int = 3,
        backoff: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,  # tests pass a MockTransport
    ):
        self.client = httpx.AsyncClient(
            headers={"User-Agent": settings.http_user_agent},
            timeout=httpx.Timeout(30, read=120),  # PDF downloads are slow
            follow_redirects=True,  # www.nseindia.com redirects to nseindia.in
            transport=transport,
        )
        self.min_interval = settings.http_min_interval if min_interval is None else min_interval
        self.max_attempts = max_attempts
        self.backoff = backoff
        self._robots: dict[str, RobotFileParser] = {}
        self._robots_lock = asyncio.Lock()
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------- public API
    async def get(self, url: str, **kwargs) -> httpx.Response:
        """GET with robots check, rate limit and retries. Raises on 4xx/5xx."""
        await self._check_robots(url)
        resp = await self._with_retries(url, lambda: self.client.get(url, **kwargs))
        resp.raise_for_status()
        return resp

    async def download(self, url: str, dest: Path) -> tuple[str, int]:
        """Stream `url` to `dest`. Returns (sha256, size in bytes).

        Writes to a .part file first, so a failed download never leaves a
        half-written file under the final name.
        """
        await self._check_robots(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")

        result: tuple[str, int] = ("", 0)

        async def attempt() -> httpx.Response:
            nonlocal result
            digest, size = hashlib.sha256(), 0
            async with self.client.stream("GET", url) as resp:
                if resp.is_error:
                    await resp.aread()
                    return resp
                with part.open("wb") as f:
                    async for chunk in resp.aiter_bytes(1 << 16):
                        f.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            result = (digest.hexdigest(), size)
            return resp

        try:
            resp = await self._with_retries(url, attempt)
            resp.raise_for_status()
            part.replace(dest)
            return result
        finally:
            part.unlink(missing_ok=True)

    # ------------------------------------------------------------- robots.txt
    async def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._robots_lock:
            if origin not in self._robots:
                self._robots[origin] = await self._fetch_robots(origin)
        if not self._robots[origin].can_fetch(settings.http_user_agent, url):
            raise RobotsDisallowed(url)

    async def _fetch_robots(self, origin: str) -> RobotFileParser:
        """Follows RFC 9309: 4xx means no rules; 5xx or unreachable means disallow all."""
        rp = RobotFileParser()
        try:
            resp = await self._with_retries(
                origin, lambda: self.client.get(f"{origin}/robots.txt")
            )
        except httpx.TransportError:
            rp.disallow_all = True
            return rp
        if resp.status_code >= 500:
            rp.disallow_all = True
        elif resp.status_code >= 400:
            rp.allow_all = True
        elif "text/html" in resp.headers.get("content-type", ""):
            # Some sites (bseindia.com) answer /robots.txt with their home page
            rp.allow_all = True
        else:
            rp.parse(resp.text.splitlines())
        return rp

    # ------------------------------------------------------------- rate limit + retries
    async def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_request.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request[host] = time.monotonic()

    async def _with_retries(self, url: str, send) -> httpx.Response:
        for attempt in range(1, self.max_attempts + 1):
            await self._throttle(url)
            try:
                resp = await send()
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    raise
            else:
                if resp.status_code not in RETRY_STATUSES or attempt == self.max_attempts:
                    return resp
            await asyncio.sleep(self.backoff * 2 ** (attempt - 1))
        raise AssertionError("unreachable")
