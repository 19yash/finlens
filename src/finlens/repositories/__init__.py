"""Data access, one repository per aggregate.

Methods are named after what the app needs (`get_for`, `set_status`), not generic
CRUD. Repositories flush but never commit: the caller owns the transaction.
"""

from finlens.repositories.companies import CompanyRepository
from finlens.repositories.reports import ReportRepository

__all__ = ["CompanyRepository", "ReportRepository"]
