"""Route tests with dependencies overridden: no database, no network.

httpx's ASGITransport calls the app in-process and doesn't run the lifespan,
so app.state is never filled; the overrides supply what routes need instead.
"""

from pathlib import Path

import httpx
import pytest

from finlens.api.deps import get_resolver
from finlens.companies.nse_master import parse_equity_list
from finlens.companies.resolver import CompanyResolver
from finlens.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "EQUITY_L.csv"


@pytest.fixture
async def client():
    resolver = CompanyResolver(parse_equity_list(FIXTURE.read_text()))
    app.dependency_overrides[get_resolver] = lambda: resolver
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_resolve_returns_company(client):
    body = (await client.get("/companies/resolve", params={"q": "Tata Consultancy"})).json()
    assert body["status"] == "resolved"
    assert body["company"] == {"isin": "INE467B01029", "symbol": "TCS",
                               "name": "Tata Consultancy Services Limited"}


async def test_resolve_ambiguous_lists_candidates(client):
    body = (await client.get("/companies/resolve", params={"q": "HDFC"})).json()
    assert body["status"] == "ambiguous" and body["company"] is None
    assert "HDFCBANK" in [c["symbol"] for c in body["candidates"]]


async def test_resolve_requires_query(client):
    assert (await client.get("/companies/resolve")).status_code == 422
