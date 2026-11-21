"""Resolve a user's company name ("Infy", "Tata Consultancy", "HDFC") to a listed company.

Fetching the wrong company's report gives a confident wrong answer, so anything
that isn't a clear single match comes back as `ambiguous` for the user to pick.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from finlens.companies.nse_master import ListedCompany

MIN_SCORE = 85  # below this a candidate isn't shown at all
CLEAR_MARGIN = 8  # the top match must beat the runner-up by this much to resolve
MAX_CANDIDATES = 5

# Everyday short forms that fuzzy matching can't reach. Values are NSE symbols.
ALIASES = {
    "sbi": "SBIN",
    "l&t": "LT",
    "l and t": "LT",
    "ril": "RELIANCE",
    "hul": "HINDUNILVR",
    "bob": "BANKBARODA",
    "airtel": "BHARTIARTL",
    "kotak": "KOTAKBANK",
    "hcl": "HCLTECH",
}

_SUFFIXES = re.compile(r"\b(limited|ltd|the|india|co|company|corporation|corp)\b")


def normalize(name: str) -> str:
    name = name.lower().replace("&", " and ")
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = _SUFFIXES.sub(" ", name)
    return " ".join(name.split())


@dataclass
class Resolution:
    status: Literal["resolved", "ambiguous", "not_found"]
    company: ListedCompany | None = None
    candidates: list[ListedCompany] = field(default_factory=list)


class CompanyResolver:
    def __init__(self, companies: list[ListedCompany]):
        self.companies = companies
        self._by_symbol = {c.symbol.lower(): c for c in companies}
        self._names = [normalize(c.name) for c in companies]
        self._by_name: dict[str, list[ListedCompany]] = {}
        for c, n in zip(companies, self._names):
            self._by_name.setdefault(n, []).append(c)

    def resolve(self, query: str) -> Resolution:
        q = query.strip()
        if len(q) < 2:
            return Resolution("not_found")

        # 1. Exact NSE symbol: "INFY", "tcs"
        if c := self._by_symbol.get(q.lower()):
            return Resolution("resolved", company=c)

        # 2. Known short form: "SBI", "L&T"
        if (sym := ALIASES.get(q.lower())) and (c := self._by_symbol.get(sym.lower())):
            return Resolution("resolved", company=c)

        # 3. Exact name after normalizing: "Infosys Ltd" -> "infosys"
        nq = normalize(q)
        if not nq:
            return Resolution("not_found")
        if (exact := self._by_name.get(nq)) and len(exact) == 1:
            return Resolution("resolved", company=exact[0])

        # 4. Fuzzy name match. token_set_ratio scores a query that is a subset of the
        #    name ("tata consultancy" in "tata consultancy services") at 100, so a short
        #    query like "hdfc" ties across HDFC Bank, HDFC Life, HDFC AMC -> ambiguous.
        matches = process.extract(
            nq, self._names, scorer=fuzz.token_set_ratio,
            score_cutoff=MIN_SCORE, limit=MAX_CANDIDATES * 4,
        )
        if not matches:
            return Resolution("not_found")

        # Among equal token_set scores, prefer names closer in length to the query
        ranked = sorted(
            matches,
            key=lambda m: (m[1], fuzz.ratio(nq, m[0])),
            reverse=True,
        )
        candidates = [self.companies[i] for _, _, i in ranked[:MAX_CANDIDATES]]
        top = ranked[0][1]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if top - runner_up >= CLEAR_MARGIN:
            return Resolution("resolved", company=candidates[0], candidates=candidates)
        return Resolution("ambiguous", candidates=candidates)
