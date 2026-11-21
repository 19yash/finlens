from fastapi import APIRouter

from finlens.api.deps import Resolver
from finlens.schemas.companies import ResolveOut

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/resolve", response_model=ResolveOut)
async def resolve_company(q: str, resolver: Resolver):
    """Match a company name or symbol; `ambiguous` means show the user a picker."""
    return resolver.resolve(q)
