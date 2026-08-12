import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

import httpx
from fastapi import Depends

from app.core.config import Settings, get_settings
from app.errors import AppError, ServiceUnavailableError

_ENVIRONMENT_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidNotConfigured(ServiceUnavailableError):
    error_type = "plaid_not_configured"


class PlaidApiError(AppError):
    status_code = 502
    error_type = "plaid_api_error"


@dataclass
class PlaidAccount:
    plaid_account_id: str
    name: str
    plaid_type: str
    plaid_subtype: str | None
    balance_minor: int


@dataclass
class PlaidTransaction:
    transaction_id: str
    plaid_account_id: str
    merchant_name: str
    amount: float  # Plaid convention: positive = money leaving the account
    txn_date: date
    pending: bool


@dataclass
class SyncResult:
    added: list[PlaidTransaction]
    modified: list[PlaidTransaction]
    removed: list[str]
    accounts: list[PlaidAccount]
    next_cursor: str
    has_more: bool


class PlaidClient(Protocol):
    async def create_link_token(self, user_id: UUID) -> str: ...

    async def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        """Returns (access_token, item_id)."""
        ...

    async def sync_transactions(self, access_token: str, cursor: str | None) -> SyncResult: ...

    async def remove_item(self, access_token: str) -> None: ...


class PlaidRestClient:
    """Calls Plaid's REST API directly via httpx, rather than the
    official plaid-python SDK — that SDK's generated client is
    synchronous (blocking urllib3 calls), which would violate this
    project's async-everywhere principle (ADR-0001) if called from a
    route handler. A direct REST client keeps us fully async with no
    extra dependency, at the cost of hand-maintaining the request/
    response shapes for the 4 endpoints actually used here."""

    def __init__(self, client_id: str, secret: str, environment: str) -> None:
        self._client_id = client_id
        self._secret = secret
        self._base_url = _ENVIRONMENT_HOSTS.get(environment, _ENVIRONMENT_HOSTS["sandbox"])

    async def _post(self, path: str, body: dict) -> dict:
        payload = {"client_id": self._client_id, "secret": self._secret, **body}

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(f"{self._base_url}{path}", json=payload)
                break
            except httpx.TransportError as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                raise PlaidApiError(
                    "Could not reach Plaid.", details={"reason": "network_error"}
                ) from exc
        else:  # pragma: no cover - unreachable, loop always breaks or raises
            raise PlaidApiError("Could not reach Plaid.") from last_error

        if response.status_code >= 400:
            error_code = None
            try:
                error_code = response.json().get("error_code")
            except ValueError:
                pass
            raise PlaidApiError(
                "Plaid API request failed.", details={"plaid_error_code": error_code}
            )

        return response.json()

    async def create_link_token(self, user_id: UUID) -> str:
        result = await self._post(
            "/link/token/create",
            {
                "user": {"client_user_id": str(user_id)},
                "client_name": "Personal Finance Platform",
                "products": ["transactions"],
                "country_codes": ["US"],
                "language": "en",
            },
        )
        return result["link_token"]

    async def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        result = await self._post(
            "/item/public_token/exchange", {"public_token": public_token}
        )
        return result["access_token"], result["item_id"]

    async def sync_transactions(self, access_token: str, cursor: str | None) -> SyncResult:
        result = await self._post(
            "/transactions/sync", {"access_token": access_token, "cursor": cursor}
        )
        return SyncResult(
            added=[_parse_transaction(t) for t in result["added"]],
            modified=[_parse_transaction(t) for t in result["modified"]],
            removed=[t["transaction_id"] for t in result["removed"]],
            accounts=[_parse_account(a) for a in result["accounts"]],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        )

    async def remove_item(self, access_token: str) -> None:
        await self._post("/item/remove", {"access_token": access_token})


def _parse_account(raw: dict) -> PlaidAccount:
    balances = raw.get("balances") or {}
    current = balances.get("current") or 0.0
    return PlaidAccount(
        plaid_account_id=raw["account_id"],
        name=raw.get("name") or raw.get("official_name") or "Account",
        plaid_type=raw.get("type") or "",
        plaid_subtype=raw.get("subtype"),
        balance_minor=round(current * 100),
    )


def _parse_transaction(raw: dict) -> PlaidTransaction:
    return PlaidTransaction(
        transaction_id=raw["transaction_id"],
        plaid_account_id=raw["account_id"],
        merchant_name=raw.get("merchant_name") or raw.get("name") or "Unknown",
        amount=raw["amount"],
        txn_date=date.fromisoformat(raw["date"]),
        pending=bool(raw.get("pending", False)),
    )


def get_plaid_client(settings: Settings = Depends(get_settings)) -> PlaidClient:
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise PlaidNotConfigured("Plaid is not configured; bank linking is unavailable.")
    return PlaidRestClient(settings.plaid_client_id, settings.plaid_secret, settings.plaid_env)
