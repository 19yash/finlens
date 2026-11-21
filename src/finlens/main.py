"""App entry point: creates the app, wires routers, and owns startup/shutdown.

    uv run uvicorn finlens.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from finlens.api.routes import companies, health
from finlens.companies.nse_master import ListedCompany
from finlens.companies.resolver import CompanyResolver
from finlens.db import Session, check_schema_current, engine
from finlens.http import HttpService
from finlens.repositories import CompanyRepository

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup: runs once, before the first request -------------------------
    await check_schema_current()  # fails fast if `alembic upgrade head` wasn't run
    async with Session() as session:
        rows = await CompanyRepository(session).all()
    if not rows:
        log.warning("companies table is empty: run `python -m finlens.companies.nse_master`")
    # ~2,600 rows: the resolver keeps them in memory
    app.state.resolver = CompanyResolver(
        [ListedCompany(r.isin, r.symbol, r.name, r.series, r.listed_on) for r in rows]
    )
    async with HttpService() as http:  # one shared client for the app's lifetime
        app.state.http = http
        yield  # --- the server handles requests while paused here ---------------
    # --- shutdown: HttpService closed by its `async with`; close DB pool too ------
    await engine.dispose()


app = FastAPI(title="FinLens", lifespan=lifespan)
app.include_router(health.router)
app.include_router(companies.router)
