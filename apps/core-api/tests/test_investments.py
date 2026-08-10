from app.investments.market_data import get_market_data_provider


async def _create_account(client, headers):
    response = await client.post(
        "/api/v1/accounts",
        json={"name": "Brokerage", "type": "investment", "currency": "USD", "current_balance_minor": 0},
        headers=headers,
    )
    return response.json()["id"]


async def _create_holding(client, headers, account_id, symbol="AAPL", quantity=10, cost_basis=150000):
    return await client.post(
        "/api/v1/investments/holdings",
        json={
            "account_id": account_id,
            "symbol": symbol,
            "name": f"{symbol} Inc.",
            "quantity": quantity,
            "cost_basis_minor": cost_basis,
        },
        headers=headers,
    )


async def test_create_holding_creates_the_security_if_new(authed_client, auth_headers):
    account_id = await _create_account(authed_client, auth_headers)
    response = await _create_holding(authed_client, auth_headers, account_id)
    assert response.status_code == 201
    body = response.json()
    assert body["security"]["symbol"] == "AAPL"
    assert body["security"]["latest_price_minor"] is None
    assert body["quantity"] == 10


async def test_holding_symbol_is_normalized_to_uppercase(authed_client, auth_headers):
    account_id = await _create_account(authed_client, auth_headers)
    response = await _create_holding(authed_client, auth_headers, account_id, symbol="aapl")
    assert response.json()["security"]["symbol"] == "AAPL"


async def test_two_holdings_same_symbol_share_one_security_row(authed_client, auth_headers):
    account_id = await _create_account(authed_client, auth_headers)
    first = await _create_holding(authed_client, auth_headers, account_id, symbol="MSFT")
    second = await _create_holding(authed_client, auth_headers, account_id, symbol="MSFT", quantity=5)
    assert first.json()["security"]["id"] == second.json()["security"]["id"]


async def test_list_and_delete_holding(authed_client, auth_headers):
    account_id = await _create_account(authed_client, auth_headers)
    create_response = await _create_holding(authed_client, auth_headers, account_id)
    holding_id = create_response.json()["id"]

    listing = await authed_client.get("/api/v1/investments/holdings", headers=auth_headers)
    assert listing.json()["total"] == 1

    delete_response = await authed_client.delete(
        f"/api/v1/investments/holdings/{holding_id}", headers=auth_headers
    )
    assert delete_response.status_code == 204

    after = await authed_client.get("/api/v1/investments/holdings", headers=auth_headers)
    assert after.json()["total"] == 0


async def test_cannot_create_holding_on_another_users_account(authed_client, auth_headers):
    other_register = await authed_client.post(
        "/api/v1/auth/register",
        json={"email": "attacker@example.com", "password": "correct horse battery"},
    )
    other_headers = {"Authorization": f"Bearer {other_register.json()['access_token']}"}
    other_account_id = await _create_account(authed_client, other_headers)

    response = await _create_holding(authed_client, auth_headers, other_account_id)
    assert response.status_code == 404


async def test_cannot_delete_another_users_holding(authed_client, auth_headers):
    account_id = await _create_account(authed_client, auth_headers)
    create_response = await _create_holding(authed_client, auth_headers, account_id)
    holding_id = create_response.json()["id"]

    other_register = await authed_client.post(
        "/api/v1/auth/register",
        json={"email": "other@example.com", "password": "correct horse battery"},
    )
    other_headers = {"Authorization": f"Bearer {other_register.json()['access_token']}"}

    response = await authed_client.delete(
        f"/api/v1/investments/holdings/{holding_id}", headers=other_headers
    )
    assert response.status_code == 404


async def test_add_watchlist_item_is_idempotent(authed_client, auth_headers):
    first = await authed_client.post(
        "/api/v1/investments/watchlist", json={"symbol": "TSLA"}, headers=auth_headers
    )
    assert first.status_code == 201

    second = await authed_client.post(
        "/api/v1/investments/watchlist", json={"symbol": "tsla"}, headers=auth_headers
    )
    assert second.status_code == 200  # not created again, but no error either
    assert second.json()["id"] == first.json()["id"]

    listing = await authed_client.get("/api/v1/investments/watchlist", headers=auth_headers)
    assert len(listing.json()) == 1


async def test_delete_watchlist_item(authed_client, auth_headers):
    create_response = await authed_client.post(
        "/api/v1/investments/watchlist", json={"symbol": "NVDA"}, headers=auth_headers
    )
    item_id = create_response.json()["id"]

    delete_response = await authed_client.delete(
        f"/api/v1/investments/watchlist/{item_id}", headers=auth_headers
    )
    assert delete_response.status_code == 204

    listing = await authed_client.get("/api/v1/investments/watchlist", headers=auth_headers)
    assert listing.json() == []


async def test_cannot_delete_another_users_watchlist_item(authed_client, auth_headers):
    create_response = await authed_client.post(
        "/api/v1/investments/watchlist", json={"symbol": "NVDA"}, headers=auth_headers
    )
    item_id = create_response.json()["id"]

    other_register = await authed_client.post(
        "/api/v1/auth/register",
        json={"email": "other2@example.com", "password": "correct horse battery"},
    )
    other_headers = {"Authorization": f"Bearer {other_register.json()['access_token']}"}

    response = await authed_client.delete(
        f"/api/v1/investments/watchlist/{item_id}", headers=other_headers
    )
    assert response.status_code == 404


async def test_refresh_prices_returns_503_when_market_data_not_configured(authed_client, auth_headers):
    await authed_client.post(
        "/api/v1/investments/watchlist", json={"symbol": "TSLA"}, headers=auth_headers
    )
    response = await authed_client.post("/api/v1/investments/prices/refresh", headers=auth_headers)
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "market_data_not_configured"


class _FakeMarketDataProvider:
    async def get_prices(self, symbols: list[str]) -> dict[str, int]:
        # Deterministic fake price per symbol, and deliberately omits one
        # symbol to prove a partial result doesn't fail the whole refresh.
        return {symbol: len(symbol) * 1000 for symbol in symbols if symbol != "UNKNOWN"}

    async def search_symbols(self, query: str):
        from app.investments.market_data import SymbolSearchResult

        if query.lower() != "tesla":
            return []
        return [SymbolSearchResult(symbol="TSLA", name="Tesla, Inc.", exchange="NASDAQ")]


async def test_refresh_prices_updates_securities_and_skips_unpriced_symbols(
    authed_client, auth_headers, app
):
    account_id = await _create_account(authed_client, auth_headers)
    await _create_holding(authed_client, auth_headers, account_id, symbol="AAPL")
    await authed_client.post(
        "/api/v1/investments/watchlist", json={"symbol": "UNKNOWN"}, headers=auth_headers
    )

    app.dependency_overrides[get_market_data_provider] = lambda: _FakeMarketDataProvider()
    response = await authed_client.post("/api/v1/investments/prices/refresh", headers=auth_headers)
    assert response.status_code == 200

    holdings = await authed_client.get("/api/v1/investments/holdings", headers=auth_headers)
    aapl = holdings.json()["items"][0]["security"]
    assert aapl["latest_price_minor"] == 4000  # len("AAPL") * 1000
    assert aapl["latest_price_at"] is not None

    watchlist = await authed_client.get("/api/v1/investments/watchlist", headers=auth_headers)
    unknown = watchlist.json()[0]["security"]
    assert unknown["latest_price_minor"] is None  # provider didn't have a price for it


async def test_search_securities_returns_503_when_market_data_not_configured(authed_client, auth_headers):
    response = await authed_client.get(
        "/api/v1/investments/securities/search?query=tesla", headers=auth_headers
    )
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "market_data_not_configured"


async def test_search_securities_returns_matches_by_company_name(authed_client, auth_headers, app):
    app.dependency_overrides[get_market_data_provider] = lambda: _FakeMarketDataProvider()

    response = await authed_client.get(
        "/api/v1/investments/securities/search?query=tesla", headers=auth_headers
    )
    assert response.status_code == 200
    results = response.json()
    assert results == [{"symbol": "TSLA", "name": "Tesla, Inc.", "exchange": "NASDAQ"}]


async def test_search_securities_returns_empty_list_for_no_matches(authed_client, auth_headers, app):
    app.dependency_overrides[get_market_data_provider] = lambda: _FakeMarketDataProvider()

    response = await authed_client.get(
        "/api/v1/investments/securities/search?query=nonexistent", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_investments_endpoints_require_auth(authed_client):
    response = await authed_client.get("/api/v1/investments/holdings")
    assert response.status_code == 401
