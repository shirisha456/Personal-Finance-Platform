from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Every event contract inherits these three fields directly (no
    separate envelope-wraps-payload structure — simpler to produce and
    to consume, with real versioning and identity built in from the
    start).

    `event_id`: lets a consumer deduplicate a redelivered message by ID
    instead of re-deriving business-key uniqueness for every event type.
    `version`: an integer schema version carried in the payload itself,
    not the topic name — see ADR-0004 for why.
    """

    event_id: UUID = Field(default_factory=uuid4)
    version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TransactionIngested(BaseEvent):
    transaction_id: UUID
    account_id: UUID
    user_id: UUID
    merchant_name: str
    amount_minor: int
    currency: str
    txn_date: date


class TransactionEnriched(BaseEvent):
    transaction_id: UUID
    account_id: UUID
    user_id: UUID
    merchant_name: str
    amount_minor: int
    currency: str
    txn_date: date
    category_id: UUID | None
    category_name: str | None
    is_recurring: bool = False


class AlertRaised(BaseEvent):
    user_id: UUID
    alert_type: Literal["duplicate_charge", "spend_spike", "subscription_price_increase"]
    severity: Literal["info", "warning", "critical"]
    title: str
    detail: str
    related_transaction_id: UUID | None = None


class InsightGenerated(BaseEvent):
    user_id: UUID
    period_start: date
    period_end: date
    summary: str


def to_json_bytes(event: BaseEvent) -> bytes:
    return event.model_dump_json().encode()
