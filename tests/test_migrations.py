"""Migrations build the schema the models describe, and can be undone.

Sync test on purpose: Alembic's env.py calls asyncio.run(), which can't run inside
a test's event loop.
"""

import pytest
from alembic import command
from alembic.config import Config

from finlens.db import ALEMBIC_INI
from tests.conftest import admin_sql, url_for

TEST_DB = "finlens_migrations_test"


@pytest.fixture
def alembic_cfg():
    try:
        admin_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin_sql(f"CREATE DATABASE {TEST_DB}")
    except OSError:
        pytest.skip("database not running (docker compose up -d db)")
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["database_url"] = url_for(TEST_DB)
    yield cfg
    admin_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


def test_upgrade_matches_models_and_downgrades(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    # Raises if models.py has changes no migration covers
    command.check(alembic_cfg)
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
