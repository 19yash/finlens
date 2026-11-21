from fastapi import APIRouter

from finlens.api.deps import Resolver

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(resolver: Resolver):
    return {"ok": True, "companies": len(resolver.companies)}
