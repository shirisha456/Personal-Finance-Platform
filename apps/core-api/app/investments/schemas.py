from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecurityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    name: str
    latest_price_minor: int | None
    latest_price_at: datetime | None


class SymbolSearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    exchange: str


class HoldingCreate(BaseModel):
    account_id: UUID
    symbol: str = Field(min_length=1, max_length=20)
    # Only used the first time this symbol is seen (get-or-create); a
    # later holding/watchlist add for the same symbol ignores it.
    name: str | None = Field(default=None, max_length=255)
    quantity: float = Field(gt=0)
    cost_basis_minor: int = Field(ge=0)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.strip().upper()


class HoldingResponse(BaseModel):
    id: UUID
    account_id: UUID
    security: SecurityResponse
    quantity: float
    cost_basis_minor: int
    created_at: datetime


class WatchlistCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=255)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.strip().upper()


class WatchlistResponse(BaseModel):
    id: UUID
    security: SecurityResponse
    created_at: datetime
