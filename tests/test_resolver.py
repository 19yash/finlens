from pathlib import Path

import pytest

from finlens.companies.nse_master import parse_equity_list
from finlens.companies.resolver import CompanyResolver

FIXTURE = Path(__file__).parent / "fixtures" / "EQUITY_L.csv"  # NSE list, 19 Nov 2026


@pytest.fixture(scope="module")
def resolver():
    return CompanyResolver(parse_equity_list(FIXTURE.read_text()))


def test_parses_full_list():
    companies = parse_equity_list(FIXTURE.read_text())
    assert len(companies) > 2500
    tcs = next(c for c in companies if c.symbol == "TCS")
    assert tcs.isin == "INE467B01029"
    assert tcs.listed_on.year == 2004


@pytest.mark.parametrize(
    "query, symbol",
    [
        ("INFY", "INFY"),
        ("Infy", "INFY"),
        ("tcs", "TCS"),
        ("Infosys", "INFY"),
        ("Infosys Ltd.", "INFY"),
        ("Tata Consultancy", "TCS"),
        ("Tata Consultancy Services", "TCS"),
        ("HDFC Bank", "HDFCBANK"),
        ("Infosis", "INFY"),  # typo
        ("Reliance", "RELIANCE"),  # also its NSE symbol
        ("SBI", "SBIN"),
        ("L&T", "LT"),
        ("State Bank", "SBIN"),
    ],
)
def test_resolves(resolver, query, symbol):
    r = resolver.resolve(query)
    assert r.status == "resolved", [c.name for c in r.candidates]
    assert r.company.symbol == symbol


@pytest.mark.parametrize("query, expected", [("HDFC", "HDFCBANK"), ("Tata", "TATASTEEL"), ("ICICI", "ICICIBANK")])
def test_ambiguous_shows_picker(resolver, query, expected):
    r = resolver.resolve(query)
    assert r.status == "ambiguous"
    assert expected in [c.symbol for c in r.candidates]


@pytest.mark.parametrize("query", ["", "x", "qwertyuiop"])
def test_not_found(resolver, query):
    assert resolver.resolve(query).status == "not_found"
