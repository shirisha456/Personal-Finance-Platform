import logging
from uuid import UUID

from meridian_events import Topics, TransactionIngested
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, AccountType
from app.core.encryption import decrypt, encrypt
from app.core.metrics import transactions_created_total
from app.core.outbox import write_outbox_event
from app.institutions.models import Institution, InstitutionStatus
from app.institutions.plaid_client import (
    PlaidAccount,
    PlaidApiError,
    PlaidClient,
    PlaidTransaction,
)
from app.transactions.models import Transaction

logger = logging.getLogger(__name__)

_PLAID_TYPE_MAP: dict[tuple[str, str | None], AccountType] = {
    ("depository", "checking"): AccountType.checking,
    ("depository", "savings"): AccountType.savings,
    ("credit", "credit card"): AccountType.credit,
    ("loan", None): AccountType.loan,
    ("investment", None): AccountType.investment,
    ("brokerage", None): AccountType.investment,
}


def _map_account_type(plaid_type: str, plaid_subtype: str | None) -> AccountType:
    if (plaid_type, plaid_subtype) in _PLAID_TYPE_MAP:
        return _PLAID_TYPE_MAP[(plaid_type, plaid_subtype)]
    if (plaid_type, None) in _PLAID_TYPE_MAP:
        return _PLAID_TYPE_MAP[(plaid_type, None)]
    return AccountType.cash


async def link_institution(
    db: AsyncSession,
    plaid_client: PlaidClient,
    user_id: UUID,
    public_token: str,
    plaid_institution_id: str | None,
    institution_name: str | None,
) -> Institution:
    access_token, item_id = await plaid_client.exchange_public_token(public_token)

    institution = Institution(
        user_id=user_id,
        plaid_item_id=item_id,
        plaid_institution_id=plaid_institution_id,
        name=institution_name or "Linked account",
        access_token_ciphertext=encrypt(access_token),
    )
    db.add(institution)
    await db.commit()
    await db.refresh(institution)

    # First sync happens inline so the account/transaction list isn't
    # empty on the very next GET. A slow first sync blocking this
    # request is an accepted tradeoff at this phase's scope (see
    # docs/phase6.md).
    await sync_institution(db, plaid_client, institution)
    return institution


async def sync_institution(db: AsyncSession, plaid_client: PlaidClient, institution: Institution) -> int:
    access_token = decrypt(institution.access_token_ciphertext)
    cursor = institution.transactions_cursor
    has_more = True
    changed = 0

    while has_more:
        try:
            result = await plaid_client.sync_transactions(access_token, cursor)
        except PlaidApiError:
            institution.status = InstitutionStatus.error
            await db.commit()
            raise

        for plaid_account in result.accounts:
            await _upsert_account(db, institution, plaid_account)

        for plaid_txn in [*result.added, *result.modified]:
            changed += await _upsert_transaction(db, institution, plaid_txn)

        for removed_id in result.removed:
            changed += await _delete_transaction(db, institution, removed_id)

        cursor = result.next_cursor
        has_more = result.has_more

    institution.transactions_cursor = cursor
    institution.status = InstitutionStatus.active  # clears a prior `error` once a sync succeeds
    await db.commit()
    return changed


async def unlink_institution(db: AsyncSession, plaid_client: PlaidClient, institution: Institution) -> None:
    try:
        access_token = decrypt(institution.access_token_ciphertext)
        await plaid_client.remove_item(access_token)
    except PlaidApiError:
        # The local unlink must still succeed even if Plaid-side removal
        # fails (e.g. Plaid is down, or the item was already removed on
        # Plaid's side) — the user's ability to disconnect an account in
        # their own app can't depend on a third party's availability.
        logger.warning(
            "Plaid item removal failed during unlink of institution %s; "
            "proceeding with local revocation anyway.",
            institution.id,
            exc_info=True,
        )

    institution.status = InstitutionStatus.revoked
    await db.commit()


async def _upsert_account(db: AsyncSession, institution: Institution, plaid_account: PlaidAccount) -> Account:
    existing = await db.scalar(
        select(Account).where(Account.plaid_account_id == plaid_account.plaid_account_id)
    )
    if existing is not None:
        existing.current_balance_minor = plaid_account.balance_minor
        existing.name = plaid_account.name
        await db.commit()
        return existing

    account = Account(
        user_id=institution.user_id,
        institution_id=institution.id,
        plaid_account_id=plaid_account.plaid_account_id,
        name=plaid_account.name,
        type=_map_account_type(plaid_account.plaid_type, plaid_account.plaid_subtype),
        current_balance_minor=plaid_account.balance_minor,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def _upsert_transaction(db: AsyncSession, institution: Institution, plaid_txn: PlaidTransaction) -> int:
    account = await db.scalar(
        select(Account).where(Account.plaid_account_id == plaid_txn.plaid_account_id)
    )
    if account is None:
        logger.warning(
            "Plaid transaction %s references unknown account %s; skipping.",
            plaid_txn.transaction_id,
            plaid_txn.plaid_account_id,
        )
        return 0

    # Plaid convention: positive amount = money leaving the account.
    # This app's convention: negative = expense. Flip once, here.
    amount_minor = -round(plaid_txn.amount * 100)

    existing = await db.scalar(
        select(Transaction).where(
            Transaction.account_id == account.id, Transaction.external_id == plaid_txn.transaction_id
        )
    )
    if existing is not None:
        existing.amount_minor = amount_minor
        existing.pending = plaid_txn.pending
        existing.merchant_name = plaid_txn.merchant_name
        await db.commit()
        return 1

    transaction = Transaction(
        account_id=account.id,
        merchant_name=plaid_txn.merchant_name,
        amount_minor=amount_minor,
        currency=account.currency,
        txn_date=plaid_txn.txn_date,
        pending=plaid_txn.pending,
        external_id=plaid_txn.transaction_id,
    )
    db.add(transaction)

    # Same event the manual-creation path publishes (app/transactions/
    # router.py) — without this, a Plaid-synced transaction never reaches
    # enrichment-service, so it can never be categorized and its category
    # stays NULL forever, regardless of how good the categorizer's rules
    # are. This was a real bug: Plaid sync never wrote to the outbox at
    # all, so every synced transaction silently skipped the entire
    # categorization pipeline.
    await db.flush()  # assigns transaction.id without committing
    event = TransactionIngested(
        transaction_id=transaction.id,
        account_id=transaction.account_id,
        user_id=institution.user_id,
        merchant_name=transaction.merchant_name,
        amount_minor=transaction.amount_minor,
        currency=transaction.currency,
        txn_date=transaction.txn_date,
    )
    write_outbox_event(
        db,
        topic=Topics.TRANSACTIONS_INGESTED,
        key=str(transaction.account_id),
        payload=event.model_dump(mode="json"),
    )

    await db.commit()
    transactions_created_total.labels(source="plaid_sync").inc()
    return 1


async def _delete_transaction(db: AsyncSession, institution: Institution, plaid_transaction_id: str) -> int:
    result = await db.execute(
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            Account.institution_id == institution.id,
            Transaction.external_id == plaid_transaction_id,
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        return 0
    await db.delete(transaction)
    await db.commit()
    return 1
