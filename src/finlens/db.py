from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finlens.config import settings

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)

# The schema is managed by Alembic: `uv run alembic upgrade head`
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request.

    Repositories never commit; the route (or job) that owns the unit of work does.
    """
    async with Session() as session:
        yield session


class SchemaOutOfDate(RuntimeError):
    pass


async def check_schema_current() -> None:
    """Raise if the database isn't at the latest migration.

    The app never migrates on startup (several workers would race); it refuses to
    run against a schema its models don't match.
    """
    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    async with engine.connect() as conn:
        current = await conn.run_sync(
            lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
        )
    if current != head:
        raise SchemaOutOfDate(
            f"database is at revision {current}, code expects {head}: "
            "run `uv run alembic upgrade head`"
        )
