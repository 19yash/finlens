"""Get one company's annual report for one fiscal year onto disk, checked.

    uv run python -m finlens.ingestion.fetcher TCS 2025

Flow: find the filing (NSE) -> download (ZIPs unpacked) -> verify company and year
-> record in `reports` with status `downloaded`. Ingestion (OCR, chunks, embeddings)
picks it up from there.

The fetcher owns its transactions: it commits after each status change, so the UI's
progress view sees `downloading` while the download runs.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finlens.config import settings
from finlens.http import HttpService
from finlens.ingestion.verify import pdf_from_zip, verify_report_pdf
from finlens.models import Report
from finlens.repositories import CompanyRepository, ReportRepository
from finlens.sources import NseReportSource, ReportSource


class FetchError(Exception):
    pass


class ReportNotFound(FetchError):
    pass


class ReportFetcher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        sources: list[ReportSource],
        http: HttpService,
        data_dir: Path | None = None,
    ):
        self.sessions = sessions
        self.sources = sources
        self.http = http
        self.reports_dir = (data_dir or settings.data_dir) / "reports"

    async def fetch(self, symbol: str, fiscal_year: int) -> Report:
        """Return the report row, downloading it unless a good copy is already on disk."""
        async with self.sessions() as session:
            company = await CompanyRepository(session).get_by_symbol(symbol)
            if company is None:
                raise FetchError(f"unknown NSE symbol {symbol!r}")
            reports = ReportRepository(session)
            report = await reports.get_for(company.isin, fiscal_year)
            if report and report.status != "failed" and report.file_path \
                    and Path(report.file_path).exists():
                return report  # cached

            candidate = None
            for source in self.sources:
                if candidate := await source.find(symbol, fiscal_year):
                    break
            if candidate is None:
                raise ReportNotFound(f"no FY{fiscal_year} annual report found for {symbol}")

            if report is None:
                report = await reports.create(company.isin, fiscal_year, candidate.source,
                                              candidate.url)
            else:
                report.source, report.source_url = candidate.source, candidate.url
            await reports.set_status(report, "downloading")
            await session.commit()

            try:
                pdf_path = self.reports_dir / symbol / f"FY{fiscal_year}.pdf"
                sha256, size = await self._download(candidate.url, pdf_path, candidate.is_zip)

                duplicate = await reports.get_by_sha256(sha256)
                if duplicate and duplicate.id != report.id:
                    raise FetchError(
                        f"file is identical to report {duplicate.id} "
                        f"({duplicate.company_isin} FY{duplicate.fiscal_year})"
                    )
                check = await asyncio.to_thread(
                    verify_report_pdf, pdf_path, company_name=company.name,
                    isin=company.isin, fiscal_year=fiscal_year,
                )
                report.file_path = str(pdf_path)
                report.sha256, report.file_size = sha256, size
                report.page_count, report.needs_review = check.page_count, check.needs_review
                await reports.set_status(report, "downloaded")
                await session.commit()
                return report
            except Exception as e:
                await session.rollback()
                await reports.set_status(report, "failed", error=f"{type(e).__name__}: {e}")
                await session.commit()
                raise

    async def _download(self, url: str, pdf_path: Path, is_zip: bool) -> tuple[str, int]:
        """Download to `pdf_path`; a ZIP is unpacked into it. Returns sha256 and size of the
        file the URL served (the filing as published, so re-fetches dedupe)."""
        if not is_zip:
            return await self.http.download(url, pdf_path)
        zip_path = pdf_path.with_suffix(".zip")
        try:
            sha256, size = await self.http.download(url, zip_path)
            await asyncio.to_thread(pdf_from_zip, zip_path, pdf_path)
        finally:
            zip_path.unlink(missing_ok=True)
        return sha256, size


async def _main(symbol: str, fiscal_year: int) -> None:
    from finlens.db import Session, engine

    async with HttpService() as http:
        fetcher = ReportFetcher(Session, [NseReportSource(http)], http)
        try:
            report = await fetcher.fetch(symbol, fiscal_year)
        finally:
            await engine.dispose()
    flag = "  (needs review: no text layer)" if report.needs_review else ""
    print(f"{symbol} FY{fiscal_year}: {report.status}, {report.page_count} pages, "
          f"{report.file_size / 1e6:.1f} MB -> {report.file_path}{flag}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m finlens.ingestion.fetcher SYMBOL FISCAL_YEAR  (e.g. TCS 2025)")
    asyncio.run(_main(sys.argv[1].upper(), int(sys.argv[2])))
