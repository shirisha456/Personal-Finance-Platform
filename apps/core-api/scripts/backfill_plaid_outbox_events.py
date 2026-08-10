"""One-off: publishes a transactions.ingested outbox event for every
Plaid-synced, still-uncategorized transaction that predates the fix in
app/institutions/service.py (Plaid sync never wrote to the outbox, so
these transactions never reached enrichment-service at all).

Naturally idempotent to re-run: a transaction that's already been
categorized (or re-categorized after this backfill) no longer matches
the WHERE clause, so running this twice just does less work the second
time, not duplicate work.

Run inside the core-api container:
    python -m scripts.backfill_plaid_outbox_events
"""

import asyncio

from meridian_events import Topics, TransactionIngested
from sqlalchemy import select

from app.accounts.models import Account
from app.core.db import AsyncSessionLocal
from app.core.outbox import write_outbox_event
from app.transactions.models import Transaction


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Transaction, Account.user_id)
            .join(Account, Account.id == Transaction.account_id)
            .where(Transaction.category_id.is_(None), Transaction.external_id.is_not(None))
        )
        rows = rows.all()

        for transaction, user_id in rows:
            event = TransactionIngested(
                transaction_id=transaction.id,
                account_id=transaction.account_id,
                user_id=user_id,
                merchant_name=transaction.merchant_name,
                amount_minor=transaction.amount_minor,
                currency=transaction.currency,
                txn_date=transaction.txn_date,
            )
            write_outbox_event(
                session,
                topic=Topics.TRANSACTIONS_INGESTED,
                key=str(transaction.account_id),
                payload=event.model_dump(mode="json"),
            )

        await session.commit()
        print(f"Published {len(rows)} backfilled transactions.ingested events.")


if __name__ == "__main__":
    asyncio.run(main())
