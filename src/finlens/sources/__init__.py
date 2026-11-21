"""Where annual reports come from. One adapter per source, tried in order."""

from finlens.sources.base import ReportCandidate, ReportSource
from finlens.sources.nse import NseReportSource

__all__ = ["NseReportSource", "ReportCandidate", "ReportSource"]
