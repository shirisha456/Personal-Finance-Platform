from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.outbox import OutboxEvent
from app.institutions.plaid_client import (
    PlaidAccount,
    PlaidApiError,
    PlaidTransaction,
    SyncResult,
    get_plaid_client,
)


class FakePlaidClient:
    def __init__(self):
        self.link_token = "fake-link-token"
        self.access_token = "fake-access-token"
        self.item_id = "fake-item-id"
        self.remove_item_calls: list[str] = []
        self.raise_remove_item = False
        self._sync_pages: list[SyncResult] = []
        self.raise_on_sync = False

    def queue_sync_page(self, page: SyncResult) -> None:
        self._sync_pages.append(page)

    async def create_link_token(self, user_id):
        return self.link_token

    async def exchange_public_token(self, public_token):
        return self.access_token, self.item_id

    async def sync_transactions(self, access_token, cursor):
        if self.raise_on_sync:
            raise PlaidApiError("simulated Plaid failure")
        return self._sync_pages.pop(0)

    async def remove_item(self, access_token):
        self.remove_item_calls.append(access_token)
        if self.raise_remove_item:
            raise PlaidApiError("simulated Plaid failure")


def _one_page(accounts=None, added=None, modified=None, removed=None, cursor="cursor-1", has_more=False):
    return SyncResult(
        added=added or [],
        modified=modified or [],
        removed=removed or [],
        accounts=accounts or [],
        next_cursor=cursor,
        has_more=has_more,
    )


def _plaid_account(plaid_account_id="plaid-acct-1", balance_minor=100000):
    return PlaidAccount(
        plaid_account_id=plaid_account_id,
        name="Plaid Checking",
        plaid_type="depository",
        plaid_subtype="checking",
        balance_minor=balance_minor,
    )


def _plaid_txn(transaction_id="plaid-txn-1", plaid_account_id="plaid-acct-1", amount=45.0):
    return PlaidTransaction(
        transaction_id=transaction_id,
        plaid_account_id=plaid_account_id,
        merchant_name="Coffee Shop",
        amount=amount,  # Plaid convention: positive = outflow
        txn_date=date(2026, 1, 15),
        pending=False,
    )


async def test_link_token_returns_503_when_plaid_not_configured(authed_client, auth_headers):
    response = await authed_client.post("/api/v1/institutions/link-token", headers=auth_headers)
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "plaid_not_configured"


async def test_link_institution_creates_institution_account_and_transaction(
    authed_client, auth_headers, app
):
    fake = FakePlaidClient()
    fake.queue_sync_page(
        _one_page(accounts=[_plaid_account()], added=[_plaid_txn()])
    )
    app.dependency_overrides[get_plaid_client] = lambda: fake

    response = await authed_client.post(
        "/api/v1/institutions",
        json={"public_token": "public-abc", "institution_id": "ins_1", "institution_name": "Chase"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Chase"
    assert body["plaid_institution_id"] == "ins_1"
    assert body["status"] == "active"
    assert "access_token_ciphertext" not in body

    accounts = await authed_client.get("/api/v1/accounts", headers=auth_headers)
    assert accounts.json()["total"] == 1
    linked_account = accounts.json()["items"][0]
    assert linked_account["current_balance_minor"] == 100000
    assert linked_account["type"] == "checking"

    transactions = await authed_client.get("/api/v1/transactions", headers=auth_headers)
    assert transactions.json()["total"] == 1
    txn = transactions.json()["items"][0]
    # Plaid amount was +45.0 (outflow); this app's convention is negative = expense.
    assert txn["amount_minor"] == -4500


async def test_synced_transaction_publishes_transactions_ingested_outbox_event(
    authed_client, auth_headers, app, db_engine
):
    """Regression test for a real bug: Plaid-synced transactions were
    created without ever writing to the outbox, so they silently skipped
    the entire categorization pipeline — every synced transaction stayed
    Uncategorized forever, no matter how good the categorizer's rules
    were, because enrichment-service never even saw them."""
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page(accounts=[_plaid_account()], added=[_plaid_txn()]))
    app.dependency_overrides[get_plaid_client] = lambda: fake

    await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        rows = (
            await session.scalars(select(OutboxEvent).where(OutboxEvent.topic == "transactions.ingested"))
        ).all()

    assert len(rows) == 1
    assert rows[0].payload["merchant_name"] == "Coffee Shop"


async def test_link_institution_falls_back_to_generic_name_without_metadata(
    authed_client, auth_headers, app
):
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page())
    app.dependency_overrides[get_plaid_client] = lambda: fake

    response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Linked account"
    assert response.json()["plaid_institution_id"] is None


async def test_sync_processes_multiple_pages_via_has_more(authed_client, auth_headers, app):
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page(accounts=[_plaid_account()]))  # initial link-time sync
    app.dependency_overrides[get_plaid_client] = lambda: fake

    link_response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    institution_id = link_response.json()["id"]

    # Queue two pages for the explicit /sync call below.
    fake.queue_sync_page(
        _one_page(
            added=[_plaid_txn(transaction_id="t1", amount=10.0)],
            cursor="cursor-2",
            has_more=True,
        )
    )
    fake.queue_sync_page(
        _one_page(
            added=[_plaid_txn(transaction_id="t2", amount=20.0)],
            cursor="cursor-3",
            has_more=False,
        )
    )

    sync_response = await authed_client.post(
        f"/api/v1/institutions/{institution_id}/sync", headers=auth_headers
    )
    assert sync_response.status_code == 200
    assert sync_response.json()["transactions_changed"] == 2  # both pages' transactions counted

    transactions = await authed_client.get("/api/v1/transactions", headers=auth_headers)
    assert transactions.json()["total"] == 2


async def test_sync_updates_existing_transaction_instead_of_duplicating(
    authed_client, auth_headers, app
):
    fake = FakePlaidClient()
    fake.queue_sync_page(
        _one_page(accounts=[_plaid_account()], added=[_plaid_txn(transaction_id="t1", amount=10.0)])
    )
    app.dependency_overrides[get_plaid_client] = lambda: fake

    link_response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    institution_id = link_response.json()["id"]

    fake.queue_sync_page(_one_page(modified=[_plaid_txn(transaction_id="t1", amount=15.0)]))
    await authed_client.post(f"/api/v1/institutions/{institution_id}/sync", headers=auth_headers)

    transactions = await authed_client.get("/api/v1/transactions", headers=auth_headers)
    assert transactions.json()["total"] == 1  # not duplicated
    assert transactions.json()["items"][0]["amount_minor"] == -1500  # updated, sign-flipped


async def test_sync_failure_sets_institution_status_to_error(authed_client, auth_headers, app):
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page())
    app.dependency_overrides[get_plaid_client] = lambda: fake

    link_response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    institution_id = link_response.json()["id"]

    fake.raise_on_sync = True
    sync_response = await authed_client.post(
        f"/api/v1/institutions/{institution_id}/sync", headers=auth_headers
    )
    assert sync_response.status_code == 502

    listing = await authed_client.get("/api/v1/institutions", headers=auth_headers)
    assert listing.json()[0]["status"] == "error"


async def test_unlink_calls_plaid_remove_item_and_revokes_locally(authed_client, auth_headers, app):
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page())
    app.dependency_overrides[get_plaid_client] = lambda: fake

    link_response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    institution_id = link_response.json()["id"]

    delete_response = await authed_client.delete(
        f"/api/v1/institutions/{institution_id}", headers=auth_headers
    )
    assert delete_response.status_code == 204
    assert fake.remove_item_calls == [fake.access_token]

    listing = await authed_client.get("/api/v1/institutions", headers=auth_headers)
    assert listing.json() == []  # revoked institutions are excluded


async def test_unlink_still_revokes_locally_even_if_plaid_remove_item_fails(
    authed_client, auth_headers, app
):
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page())
    app.dependency_overrides[get_plaid_client] = lambda: fake

    link_response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    institution_id = link_response.json()["id"]

    fake.raise_remove_item = True
    delete_response = await authed_client.delete(
        f"/api/v1/institutions/{institution_id}", headers=auth_headers
    )
    assert delete_response.status_code == 204  # local revocation succeeds regardless

    listing = await authed_client.get("/api/v1/institutions", headers=auth_headers)
    assert listing.json() == []


async def test_cannot_sync_or_unlink_another_users_institution(authed_client, auth_headers, app):
    fake = FakePlaidClient()
    fake.queue_sync_page(_one_page())
    app.dependency_overrides[get_plaid_client] = lambda: fake

    link_response = await authed_client.post(
        "/api/v1/institutions", json={"public_token": "public-abc"}, headers=auth_headers
    )
    institution_id = link_response.json()["id"]

    other_register = await authed_client.post(
        "/api/v1/auth/register",
        json={"email": "attacker@example.com", "password": "correct horse battery"},
    )
    other_headers = {"Authorization": f"Bearer {other_register.json()['access_token']}"}

    sync_response = await authed_client.post(
        f"/api/v1/institutions/{institution_id}/sync", headers=other_headers
    )
    assert sync_response.status_code == 404

    delete_response = await authed_client.delete(
        f"/api/v1/institutions/{institution_id}", headers=other_headers
    )
    assert delete_response.status_code == 404
