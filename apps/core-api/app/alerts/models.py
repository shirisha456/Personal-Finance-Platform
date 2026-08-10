import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AlertType(str, enum.Enum):
    duplicate_charge = "duplicate_charge"
    spend_spike = "spend_spike"
    subscription_price_increase = "subscription_price_increase"


class AlertSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class Alert(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        # The idempotency guarantee: without this, a redelivered
        # transactions.enriched message would create a duplicate alert
        # row (and a duplicate alerts.raised event) every time. One
        # event can legitimately
        # trigger more than one alert *type* (e.g. both a duplicate
        # charge and a spend spike on the same transaction) — the
        # constraint is on the (event, type) pair, not the event alone.
        UniqueConstraint(
            "source_event_id", "alert_type", name="uq_alerts_source_event_id_alert_type"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    alert_type: Mapped[AlertType] = mapped_column(Enum(AlertType, name="alert_type"))
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity, name="alert_severity"))
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text)
    # A real foreign key, not a bare Uuid column with no referential
    # integrity.
    related_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    # The TransactionEnriched event's event_id (ADR-0004) that produced
    # this alert — see the UniqueConstraint above.
    source_event_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
