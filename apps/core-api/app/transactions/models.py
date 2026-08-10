import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.accounts.models import Account


class Transaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        # Scoped per-account, not global — a Plaid
        # transaction_id is only guaranteed unique within its own
        # account's sync stream, and a DB-level constraint makes the
        # dedupe race-safe under concurrent syncs instead of relying on a
        # SELECT-then-insert that two concurrent requests can both pass.
        UniqueConstraint("account_id", "external_id", name="uq_transaction_account_external_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    merchant_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Negative = expense, positive = income/credit — consistent sign
    # convention across the whole app, documented once here.
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    txn_date: Mapped[date] = mapped_column(Date)
    pending: Mapped[bool] = mapped_column(Boolean, default=False)
    # Populated by Plaid sync (Phase 6); NULL for manually-entered
    # transactions, which have no external system of record to dedupe
    # against.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    account: Mapped["Account"] = relationship(back_populates="transactions")
