"""Load NSE's list of listed equities (EQUITY_L.csv) into the companies table.

    uv run python -m finlens.companies.nse_master
"""

import asyncio
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from finlens.http import HttpService

EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


@dataclass(frozen=True)
class ListedCompany:
    isin: str
    symbol: str
    name: str
    series: str
    listed_on: date | None = None


def parse_equity_list(text: str) -> list[ListedCompany]:
    # Header has stray spaces: "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, ..."
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
    companies = []
    for row in reader:
        try:
            listed_on = datetime.strptime(row["DATE OF LISTING"].strip(), "%d-%b-%Y").date()
        except ValueError:
            listed_on = None
        companies.append(
            ListedCompany(
                isin=row["ISIN NUMBER"].strip(),
                symbol=row["SYMBOL"].strip(),
                name=row["NAME OF COMPANY"].strip(),
                series=row["SERIES"].strip(),
                listed_on=listed_on,
            )
        )
    return companies


async def fetch_equity_list(http: HttpService) -> list[ListedCompany]:
    resp = await http.get(EQUITY_LIST_URL)
    return parse_equity_list(resp.text)


async def load_companies() -> int:
    from finlens.db import Session
    from finlens.repositories import CompanyRepository

    async with HttpService() as http:
        companies = await fetch_equity_list(http)
    async with Session() as session:
        count = await CompanyRepository(session).upsert_listed(companies)
        await session.commit()
    return count


if __name__ == "__main__":
    print(f"Loaded {asyncio.run(load_companies())} companies")
