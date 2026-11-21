from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ReportCandidate:
    """A report a source says it has, before anything is downloaded."""

    source: str  # "nse", "search", "upload"
    url: str
    fiscal_year: int  # year the fiscal year ends in: FY25 = 2025
    filed_at: datetime | None = None
    revised: bool = False

    @property
    def is_zip(self) -> bool:
        return self.url.lower().endswith(".zip")


class ReportSource(Protocol):
    name: str

    async def find(self, symbol: str, fiscal_year: int) -> ReportCandidate | None:
        """The best report for that company and year, or None if the source has none."""
        ...
