from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import Depends

from app.core.config import Settings, get_settings
from app.errors import ServiceUnavailableError


class MarketDataNotConfigured(ServiceUnavailableError):
    error_type = "market_data_not_configured"


@dataclass
class SymbolSearchResult:
    symbol: str
    name: str
    exchange: str


class MarketDataProvider(Protocol):
    async def get_prices(self, symbols: list[str]) -> dict[str, int]:
        """Returns {symbol: price_minor} for whichever symbols the
        provider could price — a symbol it doesn't recognize is simply
        absent from the result, not an error for the whole batch."""
        ...

    async def search_symbols(self, query: str) -> list[SymbolSearchResult]:
        """Looks up tickers by company name or partial symbol — lets a
        user type "Tesla" instead of needing to already know "TSLA"."""
        ...


class TwelveDataProvider:
    """Batches every requested symbol into one HTTP call — Twelve Data's
    free tier is rate-limited per minute, and pricing N holdings
    shouldn't cost N requests. A scheduled, rate-limit-aware poller
    across *all* users' holdings (not just one request's worth) is the
    standalone market-data-service, extracted once the event pipeline
    exists (Phase 8+) — this synchronous, on-demand refresh is Phase 5's
    scope: correct, but not yet decoupled from the request that asked
    for it."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def get_prices(self, symbols: list[str]) -> dict[str, int]:
        if not symbols:
            return {}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/price",
                params={"symbol": ",".join(symbols), "apikey": self._api_key},
            )
            response.raise_for_status()
            payload = response.json()

        # Twelve Data returns a flat {"price": "..."} object for a single
        # symbol and {SYMBOL: {"price": "..."}, ...} for multiple —
        # normalize both shapes to the same dict-of-dicts form.
        if len(symbols) == 1:
            payload = {symbols[0]: payload}

        prices: dict[str, int] = {}
        for symbol, entry in payload.items():
            if isinstance(entry, dict) and "price" in entry:
                prices[symbol] = round(float(entry["price"]) * 100)
        return prices

    async def search_symbols(self, query: str) -> list[SymbolSearchResult]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/symbol_search",
                params={"symbol": query, "apikey": self._api_key},
            )
            response.raise_for_status()
            payload = response.json()

        results: list[SymbolSearchResult] = []
        for entry in payload.get("data") or []:
            # Filtered to USD — this app has no currency-conversion model
            # for security prices (Security has no currency column;
            # get_prices() above assumes whatever it fetches is directly
            # comparable). Surfacing a EUR/GBP-denominated listing here
            # would let a user pick a symbol whose price is silently
            # wrong once treated as USD everywhere else.
            if entry.get("currency") != "USD":
                continue
            results.append(
                SymbolSearchResult(
                    symbol=entry["symbol"],
                    name=entry.get("instrument_name") or entry["symbol"],
                    exchange=entry.get("exchange") or "",
                )
            )
        return results[:10]


def get_market_data_provider(settings: Settings = Depends(get_settings)) -> MarketDataProvider:
    if not settings.market_data_api_key:
        raise MarketDataNotConfigured(
            "Market data provider is not configured; prices cannot be refreshed."
        )
    return TwelveDataProvider(settings.market_data_api_key, settings.market_data_base_url)
