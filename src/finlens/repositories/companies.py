from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from finlens.companies.nse_master import ListedCompany
from finlens.models import Company


class CompanyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def all(self) -> list[Company]:
        return list(await self.session.scalars(select(Company).order_by(Company.symbol)))

    async def get(self, isin: str) -> Company | None:
        return await self.session.get(Company, isin)

    async def get_by_symbol(self, symbol: str) -> Company | None:
        return await self.session.scalar(select(Company).where(Company.symbol == symbol))

    async def upsert_listed(self, companies: list[ListedCompany]) -> int:
        """Insert or update from the NSE list, keyed by ISIN. Keeps official_domain."""
        if not companies:
            return 0
        stmt = insert(Company).values(
            [
                {"isin": c.isin, "symbol": c.symbol, "name": c.name,
                 "series": c.series, "listed_on": c.listed_on}
                for c in companies
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Company.isin],
            set_={k: stmt.excluded[k] for k in ("symbol", "name", "series", "listed_on")},
        )
        await self.session.execute(stmt)
        return len(companies)
