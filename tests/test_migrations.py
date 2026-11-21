"""Migrations build the schema the models describe, and can be undone.

Runs on a throwaway database next to the dev one. Sync tests on purpose:
Alembic's env.py calls asyncio.run(), which can't run inside a test's event loop.
"""

import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from finlens.config import settings
from finlens.db import ALEMBIC_INI

TEST_DB = "finlens_migrations_test"


def _admin(sql: str) -> None:
    url = make_url(settings.database_url)

    async def run():
        conn = await asyncpg.connect(user=url.username, password=url.password,
                                     host=url.host, port=url.port, database=url.database)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture
def alembic_cfg():
    try:
        _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
        _admin(f"CREATE DATABASE {TEST_DB}")
    except OSError:
        pytest.skip("database not running (docker compose up -d db)")
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["database_url"] = make_url(settings.database_url).set(
        database=TEST_DB
    ).render_as_string(hide_password=False)
    yield cfg
    _admin(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


def test_upgrade_matches_models_and_downgrades(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    # Raises if models.py has changes no migration covers
    command.check(alembic_cfg)
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
