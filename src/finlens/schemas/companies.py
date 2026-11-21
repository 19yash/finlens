from typing import Literal

from pydantic import BaseModel, ConfigDict


class CompanyOut(BaseModel):
    # Lets CompanyOut.model_validate() read attributes from a ListedCompany or a
    # Company row directly, so routes need no conversion helpers
    model_config = ConfigDict(from_attributes=True)

    isin: str
    symbol: str
    name: str


class ResolveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: Literal["resolved", "ambiguous", "not_found"]
    company: CompanyOut | None
    candidates: list[CompanyOut]
