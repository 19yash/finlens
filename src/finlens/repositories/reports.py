from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finlens.models import Report

STATUSES = ("queued", "downloading", "downloaded", "parsing", "embedding", "ready", "failed")


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, report_id: int) -> Report | None:
        return await self.session.get(Report, report_id)

    async def get_for(self, isin: str, fiscal_year: int) -> Report | None:
        return await self.session.scalar(
            select(Report).where(Report.company_isin == isin, Report.fiscal_year == fiscal_year)
        )

    async def get_by_sha256(self, sha256: str) -> Report | None:
        return await self.session.scalar(select(Report).where(Report.sha256 == sha256))

    async def list_for(self, isin: str) -> list[Report]:
        stmt = select(Report).where(Report.company_isin == isin).order_by(Report.fiscal_year)
        return list(await self.session.scalars(stmt))

    async def create(self, isin: str, fiscal_year: int, source: str,
                     source_url: str | None = None) -> Report:
        report = Report(company_isin=isin, fiscal_year=fiscal_year, source=source,
                        source_url=source_url, status="queued")
        self.session.add(report)
        await self.session.flush()  # assigns report.id without committing
        return report

    async def set_status(self, report: Report, status: str, error: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        report.status, report.error = status, error
        await self.session.flush()
