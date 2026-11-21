"""Shared dependencies for routes, injected with Depends().

The objects themselves are created once in main.lifespan and kept on app.state;
these functions hand them to routes without routes importing `app` (which would
be a circular import).
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from finlens.companies.resolver import CompanyResolver
from finlens.db import get_session
from finlens.http import HttpService


def get_resolver(request: Request) -> CompanyResolver:
    return request.app.state.resolver


def get_http(request: Request) -> HttpService:
    return request.app.state.http


# Short aliases so route signatures stay readable:  def route(resolver: Resolver)
Resolver = Annotated[CompanyResolver, Depends(get_resolver)]
Http = Annotated[HttpService, Depends(get_http)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
