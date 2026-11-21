import json
from datetime import datetime
from pathlib import Path

import httpx

from finlens.http import HttpService
from finlens.sources.nse import NseReportSource, best_for_year, parse_filings

FIXTURES = Path(__file__).parent / "fixtures" / "nse"  # real API responses, 21 Nov 2026


def load(symbol: str) -> dict:
    return json.loads((FIXTURES / f"annual_reports_{symbol}.json").read_text())


def test_parses_filings_and_rewrites_file_host():
    filings = parse_filings(load("TCS"))
    assert len(filings) == 17
    fy25 = best_for_year(filings, 2025)
    assert fy25.url.startswith("https://nsearchives.nseindia.com/annual_reports/")
    assert fy25.url.endswith("_TCS_2024_2025_A_27052025233502.pdf")
    assert fy25.filed_at == datetime(2025, 5, 27, 23, 35, 2)


def test_missing_filing_dates_are_none():
    fy24 = best_for_year(parse_filings(load("TCS")), 2024)
    assert fy24.filed_at is None and fy24.is_zip


def test_revised_filing_wins():
    fy25 = best_for_year(parse_filings(load("HDFCBANK")), 2025)
    assert fy25.revised
    assert "AR_27115_HDFCBANK_2024_2025_U_" in fy25.url


def test_year_not_filed():
    assert best_for_year(parse_filings(load("TCS")), 1999) is None


async def test_find_calls_listing_api():
    seen = []

    def handler(request: httpx.Request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        seen.append(request.url)
        return httpx.Response(200, json=load("20MICRONS"))

    async with HttpService(transport=httpx.MockTransport(handler), min_interval=0) as http:
        found = await NseReportSource(http).find("20MICRONS", 2025)
    assert found.fiscal_year == 2025 and found.source == "nse"
    assert seen[0].params["symbol"] == "20MICRONS"
    assert seen[0].params["index"] == "equities"
