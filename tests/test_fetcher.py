"""ReportFetcher end to end: fake NSE (MockTransport), real migrated test database."""

import io
import zipfile

import httpx
import pytest

from finlens.companies.nse_master import ListedCompany
from finlens.http import HttpService
from finlens.ingestion.fetcher import FetchError, ReportFetcher, ReportNotFound
from finlens.ingestion.verify import VerificationError
from finlens.repositories import CompanyRepository, ReportRepository
from finlens.sources import NseReportSource
from tests.conftest import FILLER, make_pdf

TCS = ListedCompany("INE467B01029", "TCS", "Tata Consultancy Services Limited", "EQ")
FILES = "https://nsearchives.nseindia.in/annual_reports"


def filing(to_yr: int, name: str) -> dict:
    return {"fromYr": str(to_yr - 1), "toYr": str(to_yr), "submission_type": "New",
            "broadcast_dttm": "-", "fileName": f"{FILES}/{name}"}


class FakeNse:
    """Serves the listing API and the files; records every request."""

    def __init__(self, filings: list[dict], files: dict[str, bytes]):
        self.filings, self.files, self.requests = filings, files, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        self.requests.append(str(request.url))
        if request.url.path == "/api/annual-reports":
            return httpx.Response(200, json={"data": self.filings})
        assert request.url.host == "nsearchives.nseindia.com", "file host not rewritten"
        name = request.url.path.rsplit("/", 1)[-1]
        if name in self.files:
            return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)


def report_pdf(year_label: str, company: str = "Tata Consultancy Services Limited") -> bytes:
    return make_pdf([company, f"Integrated Annual Report {year_label}"] + FILLER)


@pytest.fixture
async def fetcher(sessions, tmp_path):
    async with sessions() as session:
        await CompanyRepository(session).upsert_listed([TCS])
        await session.commit()

    def build(nse: FakeNse) -> ReportFetcher:
        http = HttpService(transport=httpx.MockTransport(nse), min_interval=0, backoff=0)
        return ReportFetcher(sessions, [NseReportSource(http)], http, data_dir=tmp_path)

    return build


async def stored(sessions, year: int):
    async with sessions() as session:
        return await ReportRepository(session).get_for(TCS.isin, year)


async def test_downloads_and_records_report(fetcher, sessions, tmp_path):
    nse = FakeNse([filing(2025, "AR_TCS_2024_2025.pdf")],
                  {"AR_TCS_2024_2025.pdf": report_pdf("2024-25")})
    report = await fetcher(nse).fetch("TCS", 2025)

    assert report.status == "downloaded" and report.page_count == 3
    assert report.file_path == str(tmp_path / "reports" / "TCS" / "FY2025.pdf")
    row = await stored(sessions, 2025)
    assert row.status == "downloaded" and len(row.sha256) == 64 and not row.needs_review
    assert row.source == "nse" and row.source_url.startswith("https://nsearchives.nseindia.com/")


async def test_second_fetch_uses_cache(fetcher):
    nse = FakeNse([filing(2025, "a.pdf")], {"a.pdf": report_pdf("2024-25")})
    f = fetcher(nse)
    await f.fetch("TCS", 2025)
    before = len(nse.requests)
    await f.fetch("TCS", 2025)
    assert len(nse.requests) == before  # no listing call, no download


async def test_zip_filing_is_unpacked(fetcher):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("AR_TCS_2023_2024.pdf", report_pdf("2023-24"))
    nse = FakeNse([filing(2024, "AR_TCS_2023_2024.zip")], {"AR_TCS_2023_2024.zip": buf.getvalue()})
    report = await fetcher(nse).fetch("TCS", 2024)
    assert report.file_path.endswith("FY2024.pdf") and report.page_count == 3


async def test_wrong_company_marks_report_failed(fetcher, sessions):
    nse = FakeNse([filing(2025, "a.pdf")], {"a.pdf": report_pdf("2024-25", "Infosys Limited")})
    with pytest.raises(VerificationError):
        await fetcher(nse).fetch("TCS", 2025)
    row = await stored(sessions, 2025)
    assert row.status == "failed" and "Infosys" not in row.error
    assert "VerificationError" in row.error and row.file_path is None


async def test_failed_report_is_retried(fetcher, sessions):
    bad = FakeNse([filing(2025, "a.pdf")], {"a.pdf": report_pdf("2025-26")})
    with pytest.raises(VerificationError):
        await fetcher(bad).fetch("TCS", 2025)
    good = FakeNse([filing(2025, "b.pdf")], {"b.pdf": report_pdf("2024-25")})
    report = await fetcher(good).fetch("TCS", 2025)
    assert report.status == "downloaded" and report.error is None


async def test_same_file_for_two_years_rejected(fetcher):
    pdf = report_pdf("2024-25 and 2023-24")
    nse = FakeNse([filing(2025, "a.pdf"), filing(2024, "b.pdf")], {"a.pdf": pdf, "b.pdf": pdf})
    f = fetcher(nse)
    await f.fetch("TCS", 2025)
    with pytest.raises(FetchError, match="identical"):
        await f.fetch("TCS", 2024)


async def test_year_not_filed(fetcher):
    with pytest.raises(ReportNotFound):
        await fetcher(FakeNse([filing(2025, "a.pdf")], {})).fetch("TCS", 2019)


async def test_unknown_symbol(fetcher):
    with pytest.raises(FetchError, match="unknown NSE symbol"):
        await fetcher(FakeNse([], {})).fetch("NOPE", 2025)
