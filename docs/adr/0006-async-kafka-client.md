# ADR-0006: aiokafka + an asyncio background task, not confluent-kafka + APScheduler

## Status

Accepted

## Context

The outbox publisher needs to run periodically in the background and
call out to Kafka. A common approach is
`confluent_kafka.Producer` (a synchronous C-extension client, fire-and-
forget `produce()` + `poll(0)`, delivery confirmed via a callback) driven
by APScheduler's `BackgroundScheduler`, which runs jobs on their own OS
thread pool, separate from the FastAPI event loop. This project's async
SQLAlchemy engine (ADR-0001) is bound to the asyncio event loop; running
background work on a separate OS thread would mean either a second,
independent DB engine/session-maker for that thread, or unsafe
cross-thread use of the same async engine.

## Decision

Use `aiokafka.AIOKafkaProducer` — a genuinely async Kafka client — driven
by a plain `asyncio.create_task` loop started in the FastAPI lifespan,
running on the same event loop as every request handler. No second
scheduler library, no cross-thread database access.

`producer.send_and_wait(topic, value, key)` is the key primitive this
unlocks: it's a single awaitable that only returns once the broker has
acknowledged the message (or raises on failure) — which is also what
closes the ordering bug ADR-0005 describes, where a row could be marked
`published` before delivery was actually confirmed.
`confluent-kafka`'s callback-based model makes that same guarantee
possible too, but only with more bookkeeping (tracking a delivery
future per in-flight message); `send_and_wait` gives it for free.

## Alternatives considered

- **confluent-kafka + APScheduler** — rejected per Context: introduces
  either a second database
  connection story or genuine thread-safety risk with the existing async
  engine, for a library that's arguably harder to use correctly
  (explicit `poll()`/`flush()` calls, callback-based delivery
  confirmation) than `aiokafka`'s native async API.
- **confluent-kafka on the main event loop via `asyncio.to_thread`** —
  possible, but adds a layer of indirection (wrapping each blocking call)
  to get back to roughly the same place `aiokafka` starts at natively.

## Consequences

- `aiokafka` is a smaller, less corporately-backed project than
  `confluent-kafka` (which wraps librdkafka, the C library most
  production Kafka tooling is built on). Accepted as a
  reasonable tradeoff for a project of this scale, where async-engine
  compatibility and code simplicity matter more than
  librdkafka-specific performance tuning options this app doesn't need.
- The producer connects lazily (`app/core/kafka.py::get_kafka_producer`)
  on the publisher loop's first iteration, not at app startup — so the
  API still boots and serves every non-Kafka request normally even if
  Redpanda isn't running.

## Validation

`apps/core-api/tests/test_outbox.py` uses a fake producer satisfying the
`KafkaProducer` protocol (structural typing — no aiokafka-specific test
dependency needed). End-to-end verification against a real Redpanda
broker is documented in `docs/phase7.md`.
