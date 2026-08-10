import json
from datetime import date
from uuid import uuid4

from meridian_events import TransactionEnriched, to_json_bytes

from app.consumer import process_message
from app.db import accounts_table, alerts_table, transactions_table


class FakeProducer:
    def __init__(self):
        self.sent: list[tuple[str, bytes, bytes | None, list[tuple[str, bytes]] | None]] = []

    async def send_and_wait(self, topic, value, key=None, headers=None):
        self.sent.append((topic, value, key, headers))


async def _insert_account(session, account_id, user_id):
    await session.execute(accounts_table.insert().values(id=account_id, user_id=user_id))
    await session.commit()


async def _insert_txn(session, txn_id, account_id, merchant_name, amount_minor, txn_date):
    await session.execute(
        transactions_table.insert().values(
            id=txn_id, account_id=account_id, category_id=None,
            merchant_name=merchant_name, amount_minor=amount_minor, txn_date=txn_date,
        )
    )
    await session.commit()


def _payload(**overrides):
    defaults = {
        "transaction_id": uuid4(),
        "account_id": uuid4(),
        "user_id": uuid4(),
        "merchant_name": "Coffee Shop",
        "amount_minor": -450,
        "currency": "USD",
        "txn_date": date(2026, 1, 15),
        "category_id": None,
        "category_name": None,
        "is_recurring": False,
    }
    defaults.update(overrides)
    return to_json_bytes(TransactionEnriched(**defaults))


async def test_process_message_raises_no_alert_for_an_unremarkable_transaction(session_factory):
    producer = FakeProducer()
    raised = await process_message(_payload(), session_factory, producer)
    assert raised == 0
    assert producer.sent == []


async def test_process_message_raises_and_publishes_a_duplicate_charge_alert(session_factory):
    account_id = uuid4()
    async with session_factory() as session:
        await _insert_account(session, account_id, uuid4())
        await _insert_txn(session, uuid4(), account_id, "Coffee Shop", -450, date(2026, 1, 15))

    producer = FakeProducer()
    payload = _payload(account_id=account_id, merchant_name="Coffee Shop", amount_minor=-450)
    raised = await process_message(payload, session_factory, producer)

    assert raised == 1
    assert len(producer.sent) == 1
    topic, value, _key, _headers = producer.sent[0]
    assert topic == "alerts.raised"
    alert = json.loads(value)
    assert alert["alert_type"] == "duplicate_charge"


async def test_reprocessing_the_same_event_does_not_create_a_duplicate_alert(session_factory):
    """The actual idempotency guarantee this service is built to hold:
    without the (event_id, alert_type) uniqueness check, a redelivered
    message would create a second, distinct alert row every time. This
    test proves the constraint actually prevents that."""
    account_id = uuid4()
    async with session_factory() as session:
        await _insert_account(session, account_id, uuid4())
        await _insert_txn(session, uuid4(), account_id, "Coffee Shop", -450, date(2026, 1, 15))

    producer = FakeProducer()
    payload = _payload(account_id=account_id, merchant_name="Coffee Shop", amount_minor=-450)

    first_raised = await process_message(payload, session_factory, producer)
    second_raised = await process_message(payload, session_factory, producer)  # simulated redelivery

    assert first_raised == 1
    assert second_raised == 0  # the second attempt recognizes the alert already exists
    assert len(producer.sent) == 1  # only published once, not twice

    async with session_factory() as session:
        count = (
            await session.execute(
                alerts_table.select().where(alerts_table.c.alert_type == "duplicate_charge")
            )
        ).all()
    assert len(count) == 1  # exactly one row in the database, not two


async def test_a_transaction_can_raise_multiple_distinct_alert_types_from_one_event(session_factory):
    """A single event legitimately triggering two different alert_types
    must not be blocked by the same uniqueness constraint that prevents
    redelivery duplicates — the constraint is on (event_id, alert_type),
    not on event_id alone."""
    account_id, user_id, category_id = uuid4(), uuid4(), uuid4()
    async with session_factory() as session:
        await _insert_account(session, account_id, user_id)
        # History that makes this transaction BOTH a duplicate charge
        # AND a category spend spike.
        await session.execute(
            transactions_table.insert().values(
                id=uuid4(), account_id=account_id, category_id=category_id,
                merchant_name="Coffee Shop", amount_minor=-50000, txn_date=date(2026, 1, 15),
            )
        )
        for i in range(5):
            await session.execute(
                transactions_table.insert().values(
                    id=uuid4(), account_id=account_id, category_id=category_id,
                    merchant_name="Other Merchant", amount_minor=-2000, txn_date=date(2026, 1, 1 + i),
                )
            )
        await session.commit()

    producer = FakeProducer()
    payload = _payload(
        account_id=account_id, user_id=user_id, category_id=category_id,
        merchant_name="Coffee Shop", amount_minor=-50000, txn_date=date(2026, 1, 15),
    )
    raised = await process_message(payload, session_factory, producer)

    assert raised == 2  # duplicate_charge AND spend_spike, both from one event
    published_types = {json.loads(v)["alert_type"] for _, v, _, _ in producer.sent}
    assert published_types == {"duplicate_charge", "spend_spike"}
