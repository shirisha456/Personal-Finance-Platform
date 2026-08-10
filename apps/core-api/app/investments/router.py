from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account
from app.auth.deps import get_current_user
from app.auth.models import User
from app.core.db import get_db
from app.core.ownership import get_owned
from app.core.pagination import Page, Pagination
from app.errors import NotFoundError
from app.investments import service
from app.investments.market_data import MarketDataProvider, get_market_data_provider
from app.investments.models import Holding, Security, Watchlist
from app.investments.schemas import (
    HoldingCreate,
    HoldingResponse,
    SecurityResponse,
    SymbolSearchResponse,
    WatchlistCreate,
    WatchlistResponse,
)

router = APIRouter(prefix="/api/v1/investments", tags=["investments"])


async def _get_owned_holding(db: AsyncSession, holding_id: UUID, user_id: UUID) -> Holding:
    # Same join-through-account pattern as transactions — holdings have
    # no user_id column of their own.
    holding = await db.get(Holding, holding_id)
    if holding is None:
        raise NotFoundError("Holding not found.")
    account = await db.get(Account, holding.account_id)
    if account is None or account.user_id != user_id:
        raise NotFoundError("Holding not found.")
    return holding


def _holding_response(holding: Holding, security: Security) -> HoldingResponse:
    return HoldingResponse(
        id=holding.id,
        account_id=holding.account_id,
        security=SecurityResponse.model_validate(security),
        quantity=holding.quantity,
        cost_basis_minor=holding.cost_basis_minor,
        created_at=holding.created_at,
    )


def _watchlist_response(item: Watchlist, security: Security) -> WatchlistResponse:
    return WatchlistResponse(
        id=item.id,
        security=SecurityResponse.model_validate(security),
        created_at=item.created_at,
    )


@router.post("/holdings", status_code=status.HTTP_201_CREATED, response_model=HoldingResponse)
async def create_holding(
    body: HoldingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HoldingResponse:
    account = await get_owned(db, Account, body.account_id, current_user.id)
    security = await service.get_or_create_security(db, body.symbol, body.name)

    holding = Holding(
        account_id=account.id,
        security_id=security.id,
        quantity=body.quantity,
        cost_basis_minor=body.cost_basis_minor,
    )
    db.add(holding)
    await db.commit()
    await db.refresh(holding)
    return _holding_response(holding, security)


@router.get("/holdings", response_model=Page[HoldingResponse])
async def list_holdings(
    pagination: Pagination = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Page[HoldingResponse]:
    base_query = (
        select(Holding, Security)
        .join(Account, Account.id == Holding.account_id)
        .join(Security, Security.id == Holding.security_id)
        .where(Account.user_id == current_user.id)
    )

    total = await db.scalar(select(func.count()).select_from(base_query.subquery()))
    result = await db.execute(
        base_query.order_by(Holding.created_at).limit(pagination.limit).offset(pagination.offset)
    )

    return Page(
        items=[_holding_response(holding, security) for holding, security in result],
        total=total or 0,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding(
    holding_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    holding = await _get_owned_holding(db, holding_id, current_user.id)
    await db.delete(holding)
    await db.commit()


@router.post("/watchlist", response_model=WatchlistResponse)
async def add_watchlist_item(
    body: WatchlistCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WatchlistResponse:
    security = await service.get_or_create_security(db, body.symbol, body.name)

    existing = await db.scalar(
        select(Watchlist).where(
            Watchlist.user_id == current_user.id, Watchlist.security_id == security.id
        )
    )
    if existing is not None:
        # Idempotent add: the same symbol watchlisted twice returns the
        # existing row rather than erroring or duplicating — 200, not 201,
        # since nothing was actually created this time.
        response.status_code = status.HTTP_200_OK
        return _watchlist_response(existing, security)

    item = Watchlist(user_id=current_user.id, security_id=security.id)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    response.status_code = status.HTTP_201_CREATED
    return _watchlist_response(item, security)


@router.get("/watchlist", response_model=list[WatchlistResponse])
async def list_watchlist(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[WatchlistResponse]:
    result = await db.execute(
        select(Watchlist, Security)
        .join(Security, Security.id == Watchlist.security_id)
        .where(Watchlist.user_id == current_user.id)
        .order_by(Security.symbol)
    )
    return [_watchlist_response(item, security) for item, security in result]


@router.delete("/watchlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    item = await get_owned(db, Watchlist, item_id, current_user.id)
    await db.delete(item)
    await db.commit()


@router.get("/securities/search", response_model=list[SymbolSearchResponse])
async def search_securities(
    query: str = Query(min_length=1, max_length=100),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> list[SymbolSearchResponse]:
    results = await provider.search_symbols(query)
    return [SymbolSearchResponse(symbol=r.symbol, name=r.name, exchange=r.exchange) for r in results]


@router.post("/prices/refresh", response_model=list[SecurityResponse])
async def refresh_prices(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    provider: MarketDataProvider = Depends(get_market_data_provider),
) -> list[Security]:
    held_security_ids = (
        select(Holding.security_id)
        .join(Account, Account.id == Holding.account_id)
        .where(Account.user_id == current_user.id)
    )
    watchlisted_security_ids = select(Watchlist.security_id).where(
        Watchlist.user_id == current_user.id
    )
    relevant_security_ids = held_security_ids.union(watchlisted_security_ids)

    securities = list(
        await db.scalars(select(Security).where(Security.id.in_(relevant_security_ids)))
    )
    if not securities:
        return []

    prices = await provider.get_prices([s.symbol for s in securities])

    now = datetime.now(UTC)
    updated = []
    for security in securities:
        if security.symbol in prices:
            security.latest_price_minor = prices[security.symbol]
            security.latest_price_at = now
            updated.append(security)

    await db.commit()
    for security in updated:
        await db.refresh(security)
    return securities
