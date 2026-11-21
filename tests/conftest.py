"""Shared fixtures.

`test_db_url` builds a throwaway database next to the dev one and migrates it, for
tests of code that commits (the fetcher) and so can't use a rolled-back transaction.
"""

import asyncio

import asyncpg
import pymupdf
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from finlens.config import settings

TEST_DB = "finlens_test"


def admin_sql(sql: str) -> None:
    """Run one statement against the dev database's server (CREATE/DROP DATABASE)."""
    url = make_url(settings.database_url)

    async def run():
        conn = await asyncpg.connect(user=url.username, password=url.password,
                                     host=url.host, port=url.port, database=url.database)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(run())


def url_for(database: str) -> str:
    return make_url(settings.database_url).set(database=database).render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="session")
def test_db_url():
    from alembic import command
    from alembic.config import Config

    from finlens.db import ALEMBIC_INI

    try:
        admin_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin_sql(f"CREATE DATABASE {TEST_DB}")
    except OSError:
        pytest.skip("database not running (docker compose up -d db)")
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["database_url"] = url_for(TEST_DB)
    command.upgrade(cfg, "head")
    yield url_for(TEST_DB)
    admin_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture
async def sessions(test_db_url):
    """A session factory on the test database, emptied after each test."""
    engine = create_async_engine(test_db_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE facts, chunks, reports, companies CASCADE"))
    await engine.dispose()


def make_pdf(lines: list[str], pages: int = 3) -> bytes:
    """A small text PDF; every page carries `lines`."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 16
    data = doc.tobytes()
    doc.close()
    return data


FILLER = ["Management discussion and analysis of the year's operations and results."] * 4
