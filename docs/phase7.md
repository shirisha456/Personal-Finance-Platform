# Phase 7 — Transactional Outbox and Events

## Goal

A real, installable shared event-contracts package (`libs/events`), the
transactional outbox pattern in `core-api`, and a working Kafka producer
— proven against a real Redpanda broker, not just unit-tested against a
fake. This is the foundation Phases 8–10 (enrichment, anomaly detection,
insights) consume directly.

## Scope: which topics exist

Four topics — `transactions.ingested`, `transactions.enriched`,
`alerts.raised`, `insights.generated` — matching exactly what Phases
8–10 will produce and consume. Two more candidate topics,
`prices.updated` and `networth.snapshot.completed`, are deliberately
not defined yet. Since net worth here is computed synchronously
(Phase 4) and market data is an on-demand endpoint (Phase 5), not a
Kafka producer, and the phase plan has no slot for extracting a
standalone market-data-service, defining those two topics now would
create a dead contract — an event schema with no consumer, silently
untested and unused. If a standalone market-data poller is wanted
later, that's a real scope decision to make explicitly then — not
something to quietly decide by defining an unused topic today.

## Architecture

```
libs/events/                    Real installable package (meridian-events),
                                 not a PYTHONPATH hack — see Design decisions.
  meridian_events/
    schemas.py                  BaseEvent (event_id, version, occurred_at)
                                 + TransactionIngested/Enriched, AlertRaised,
                                 InsightGenerated
    topics.py                   Topics — the only place a topic string is spelled

apps/core-api/app/core/
  outbox.py                     OutboxEvent model + write_outbox_event()
                                 (adds to session, does not commit)
  kafka.py                      KafkaProducer protocol + aiokafka-backed
                                 get_kafka_producer() (lazy-connects)
  outbox_publisher.py           publish_pending_outbox_events() +
                                 run_outbox_publisher_loop() (asyncio task,
                                 3s interval, started in the app lifespan)

POST /api/v1/transactions       → if category_id is None: writes a
                                   TransactionIngested row in the SAME
                                   transaction as the Transaction insert
                                   (app/transactions/router.py)
```

## Design decisions

See [ADR-0004](adr/0004-event-contract-versioning.md) (versioning),
[ADR-0005](adr/0005-transactional-outbox.md) (outbox vs. dual-write vs.
CDC), and [ADR-0006](adr/0006-async-kafka-client.md) (aiokafka vs.
confluent-kafka+APScheduler) for the three decisions substantial enough
to warrant their own record. Summarized:

| Decision | Choice | Rejected alternative |
|---|---|---|
| `libs/events` packaging | A real installable package (`pip install -e libs/events`), installed as an explicit separate step since a relative-path dependency isn't reliably expressible in `pyproject.toml` | `PYTHONPATH` hacks — works, but isn't a real package boundary; nothing enforces `libs/events`' own dependencies or lets it be versioned independently |
| Event versioning | `version: int` field in the payload (ADR-0004) | Version in the topic name, or no versioning at all |
| Kafka client | `aiokafka` + `asyncio.create_task` (ADR-0006) | `confluent-kafka` + APScheduler `BackgroundScheduler` — thread-safety risk against the async DB engine |
| Outbox publish-then-mark ordering | A row is marked `published` in memory only *after* `send_and_wait` confirms delivery | Marking `published=True` before delivery is confirmed — a real, silent event-loss risk if the process crashes in between |
| Which topics exist | Only the 4 that Phases 8-10 actually use | Also defining `prices.updated`/`networth.snapshot.completed` now, unused, "for later" |

## Tradeoffs

- `libs/events` not being a declared `pyproject.toml` dependency of
  `apps/core-api` means nothing enforces version compatibility between
  them automatically — a real cost of not adopting a workspace tool
  (`uv`, `pdm`) at this project's current size. Explicitly accepted, not
  hidden; revisit if this repo ever needs more than 2-3 internal
  packages depending on each other.
- The outbox publisher's batch commit (all successfully-sent rows in one
  `db.commit()` at the end of a cycle) means a crash between a confirmed
  send and that commit causes one specific, bounded failure mode: that
  event gets republished (and its consumer sees a duplicate) on the next
  cycle. This is genuine at-least-once — never loss — and is exactly why
  Phase 8/9 consumers need to be idempotent using `event_id`, not
  incidental.
- 3-second poll interval is a deliberate latency/simplicity tradeoff, not
  tuned against any specific requirement — revisit if a future phase's
  UX needs categorization to feel closer to instant.

## Verification checklist

- [x] `libs/events` has its own venv, tests, and lint — `pytest -v` → 6
      passed, `ruff check .` → clean, run as its own CI job
- [x] `alembic revision --autogenerate` produced a clean `outbox_events`
      table; full `upgrade → downgrade base → upgrade` cycle verified
      against real Postgres, zero errors
- [x] `pytest -v` (core-api) — 98 tests passing (92 from Phases 1-6 + 6
      new): outbox row stays unpublished until the publisher runs, a
      successful publish marks it published, a producer failure on one
      row doesn't block others in the same batch and leaves it for retry,
      an already-published row is never republished, an uncategorized
      transaction writes exactly one `TransactionIngested` row, a
      pre-categorized one writes none
- [x] `ruff check .` — clean
- [x] **Full pipeline verified against a real Redpanda broker**, not
      just a fake producer: `docker compose up postgres redis redpanda
      redpanda-topics` → confirmed all 4 topics created → booted the
      real app → registered a user, created an account, posted an
      uncategorized transaction → waited one publisher cycle → `rpk
      topic consume transactions.ingested` returned the actual message,
      with the correct key (`account_id`), a real `event_id`,
      `version: 1`, and every transaction field matching what was
      posted → confirmed the `outbox_events` row's `published` flag was
      `true` in Postgres afterward
