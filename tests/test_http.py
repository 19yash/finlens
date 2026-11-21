import time

import httpx
import pytest

from finlens.http import HttpService, RobotsDisallowed

ROBOTS = "User-agent: *\nDisallow: /private\n"


def service(handler, **kw) -> HttpService:
    kw.setdefault("min_interval", 0)
    kw.setdefault("backoff", 0)
    return HttpService(transport=httpx.MockTransport(handler), **kw)


def robots_then(handler, robots=ROBOTS, robots_status=200):
    def wrapped(request: httpx.Request):
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots)
        return handler(request)
    return wrapped


async def test_get_allowed_path():
    async with service(robots_then(lambda r: httpx.Response(200, text="ok"))) as http:
        assert (await http.get("https://example.com/public")).text == "ok"


async def test_robots_disallow_raises_and_never_requests():
    hits = []
    def handler(request):
        hits.append(request.url.path)
        return httpx.Response(200)
    async with service(robots_then(handler)) as http:
        with pytest.raises(RobotsDisallowed):
            await http.get("https://example.com/private/report.pdf")
    assert hits == []


@pytest.mark.parametrize("status, allowed", [(404, True), (503, False)])
async def test_robots_missing_vs_server_error(status, allowed):
    """RFC 9309: 4xx means no rules; 5xx means assume everything is disallowed."""
    async with service(robots_then(lambda r: httpx.Response(200), robots_status=status),
                       max_attempts=1) as http:
        if allowed:
            await http.get("https://example.com/private")
        else:
            with pytest.raises(RobotsDisallowed):
                await http.get("https://example.com/anything")


async def test_robots_html_page_treated_as_no_rules():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, html="<html>Disallow: /</html>")
        return httpx.Response(200)
    async with service(handler) as http:
        await http.get("https://bse.example/page")


async def test_retries_transient_errors_then_succeeds():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else httpx.Response(200, text="ok")
    async with service(robots_then(handler)) as http:
        assert (await http.get("https://example.com/x")).text == "ok"
    assert calls == 3


async def test_does_not_retry_404():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404)
    async with service(robots_then(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await http.get("https://example.com/missing")
    assert calls == 1


async def test_rate_limit_spaces_requests_to_same_host():
    async with service(robots_then(lambda r: httpx.Response(200)), min_interval=0.2) as http:
        await http.get("https://example.com/a")  # robots.txt + a
        start = time.monotonic()
        await http.get("https://example.com/b")
        await http.get("https://example.com/c")
        assert time.monotonic() - start >= 0.38


async def test_download_streams_to_disk_with_hash(tmp_path):
    import hashlib
    body = b"%PDF-1.7 " + b"x" * 200_000
    async with service(robots_then(lambda r: httpx.Response(200, content=body))) as http:
        sha, size = await http.download("https://example.com/r.pdf", tmp_path / "r.pdf")
    assert (tmp_path / "r.pdf").read_bytes() == body
    assert (sha, size) == (hashlib.sha256(body).hexdigest(), len(body))
    assert not (tmp_path / "r.pdf.part").exists()


async def test_failed_download_leaves_no_file(tmp_path):
    async with service(robots_then(lambda r: httpx.Response(500)), max_attempts=2) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await http.download("https://example.com/r.pdf", tmp_path / "r.pdf")
    assert list(tmp_path.iterdir()) == []
