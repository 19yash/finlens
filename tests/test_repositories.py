"""Runs against the docker-compose database; each test is rolled back."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from finlens.companies.nse_master import ListedCompany
from finlens.config import settings
from finlens.repositories import CompanyRepository, ReportRepository

TEST = ListedCompany("INTEST000001", "ZZTEST", "Zz Test Limited", "EQ")


@pytest.fixture
async def session():
    engine = create_async_engine(settings.database_url)
    try:
        conn = await engine.connect()
    except OSError:
        await engine.dispose()
        pytest.skip("database not running (docker compose up -d db)")
    trans = await conn.begin()
    # Repositories only flush, so everything here is undone by the rollback
    yield AsyncSession(bind=conn, expire_on_commit=False)
    await trans.rollback()
    await conn.close()
    await engine.dispose()


async def test_upsert_keeps_official_domain(session):
    companies = CompanyRepository(session)
    await companies.upsert_listed([TEST])
    company = await companies.get_by_symbol("ZZTEST")
    company.official_domain = "zztest.example"
    await session.flush()

    await companies.upsert_listed([ListedCompany(TEST.isin, "ZZTEST", "Zz Renamed Ltd", "EQ")])
    session.expire_all()
    company = await companies.get(TEST.isin)
    assert company.name == "Zz Renamed Ltd"
    assert company.official_domain == "zztest.example"


async def test_report_lifecycle(session):
    await CompanyRepository(session).upsert_listed([TEST])
    reports = ReportRepository(session)
    report = await reports.create(TEST.isin, 2025, "nse", "https://example/ar.pdf")
    assert report.id is not None and report.status == "queued"

    await reports.set_status(report, "downloading")
    assert (await reports.get_for(TEST.isin, 2025)).status == "downloading"
    with pytest.raises(ValueError):
        await reports.set_status(report, "done")
