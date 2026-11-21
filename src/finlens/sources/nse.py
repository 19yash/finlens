"""NSE annual-report filings: every NSE-listed company must file here (SEBI LODR Reg. 34).

See docs/sources.md for how this API was checked.
"""

from datetime import datetime

from finlens.http import HttpService
from finlens.sources.base import ReportCandidate

LIST_URL = "https://www.nseindia.com/api/annual-reports"


def _parse_dt(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%d-%b-%Y %H:%M:%S")
    except ValueError:
        return None  # NSE sends "-" for many older filings


def _file_url(url: str) -> str:
    # Links point at nsearchives.nseindia.in, which times out; the .com host serves
    # the same files
    return url.replace("://nsearchives.nseindia.in/", "://nsearchives.nseindia.com/")


def parse_filings(payload: dict) -> list[ReportCandidate]:
    candidates = []
    for row in payload.get("data", []):
        try:
            fiscal_year = int(row["toYr"])
        except (KeyError, TypeError, ValueError):
            continue
        if not row.get("fileName"):
            continue
        candidates.append(
            ReportCandidate(
                source="nse",
                url=_file_url(row["fileName"]),
                fiscal_year=fiscal_year,
                filed_at=_parse_dt(row.get("broadcast_dttm")),
                revised=row.get("submission_type") == "Revised",
            )
        )
    return candidates


def best_for_year(candidates: list[ReportCandidate], fiscal_year: int) -> ReportCandidate | None:
    """A revised filing beats the original; then the latest filing date wins."""
    same_year = [c for c in candidates if c.fiscal_year == fiscal_year]
    if not same_year:
        return None
    return max(same_year, key=lambda c: (c.revised, c.filed_at or datetime.min))


class NseReportSource:
    name = "nse"

    def __init__(self, http: HttpService):
        self.http = http

    async def list(self, symbol: str) -> list[ReportCandidate]:
        resp = await self.http.get(LIST_URL, params={"index": "equities", "symbol": symbol})
        return parse_filings(resp.json())

    async def find(self, symbol: str, fiscal_year: int) -> ReportCandidate | None:
        return best_for_year(await self.list(symbol), fiscal_year)
