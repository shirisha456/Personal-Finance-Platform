# Phase 9 — Anomaly Detection and Notifications

## Goal

Two more standalone Kafka consumers (`anomaly-service`, `notification-service`)
plus real-time delivery: `core-api` gains an `alerts` table and a
WebSocket endpoint, so a detected anomaly reaches the browser live,
without a page refresh — verified end-to-end against real infrastructure,
not simulated.

## The idempotency guarantee this phase actually proves

It's easy to *claim* a consumer is idempotent in an ADR without the code
actually holding that guarantee. Inserting a fresh `uuid4()` alert row
on every call with no uniqueness check would mean a redelivered
`transactions.enriched` message (a real possibility under the outbox's
at-least-once delivery, per ADR-0005) creates a genuine duplicate alert
every time. Idempotency has to be provably true, not just documented as
true — that's the property this phase is built to guarantee from the
start, with a test that proves it, not just a comment claiming it.

The guarantee: `alerts.source_event_id` (the `TransactionEnriched` event's
`event_id` — ADR-0004 built this in specifically for this purpose),
constrained `UNIQUE (source_event_id, alert_type)`. A redelivered event
re-evaluates the same rules, finds the alert already exists, and skips
it — proven by `test_reprocessing_the_same_event_does_not_create_a_duplicate_alert`
in `services/anomaly-service/tests/test_consumer.py`, and by a second
test proving the same constraint doesn't block one transaction from
legitimately raising two *different* alert types.

## Architecture

```
transactions.enriched
  → anomaly-service/app/rules.py evaluates 3 rules per message:
      detect_duplicate_charge          same account+merchant+amount within a day
      detect_spend_spike                >3x the category's 90-day average,
                                          min 5 prior transactions
      detect_subscription_price_increase  >5% higher than the same recurring
                                            merchant's previous charge
  → for each rule that fires: alert_exists(event_id, alert_type) check,
    then insert_alert (shared-DB write into core-api's `alerts` table,
    ADR-0007) + publish alerts.raised

alerts.raised, insights.generated
  → notification-service/app/consumer.py: Topics → notification "type"
    mapping (imports Topics, doesn't hardcode topic-name strings — that
    would be a real drift risk against `libs/events`)
  → Redis PUBLISH to notifications:{user_id}

core-api:
  POST /api/v1/auth/ws-ticket (authenticated) → a 30-second, single-use
    ticket (app/core/ws_tickets.py)
  WS /ws/live?ticket=...       → redeems the ticket, subscribes to
    notifications:{user_id}, relays every message to the browser
  GET /api/v1/alerts, PATCH /api/v1/alerts/{id}/read → the persisted,
    authoritative record (the WebSocket is a live nicety on top of this,
    not the system of record)
```

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Anomaly idempotency | `UNIQUE (source_event_id, alert_type)` backed by the real `event_id` from ADR-0004 | No uniqueness check at all — the headline correctness property this phase is built to guarantee |
| `related_transaction_id` | A real foreign key to `transactions.id` (`ON DELETE SET NULL`) | A bare `Uuid` column with no referential integrity |
| WebSocket auth | A 30-second, single-use ticket issued by an authenticated `POST /auth/ws-ticket`, not the long-lived access token, in the WS URL | Putting the long-lived access token directly in the WS URL (`?token=<access_token>`) — browsers can't set custom WS headers, so *something* goes in the URL, but a long-lived bearer token there is a real log/proxy-history exposure a short-lived single-use ticket meaningfully narrows |
| `alerts` table ownership | `core-api` owns and migrates it; `anomaly-service` writes to it via the same minimal-column-subset pattern as `enrichment-service` reading `transactions` (ADR-0007) | A separate alerts-service with its own database — more infrastructure than 3 rule types justify |
| No `POST /alerts` in core-api | Deliberate — alerts are only ever created by `anomaly-service`'s direct DB write | Adding a create endpoint "for completeness" — would be dead code nothing calls, and a route that bypasses the actual detection logic if anyone did call it |

## Tradeoffs

- No dead-letter queue for `anomaly-service` or `notification-service` —
  same accepted, documented gap as `enrichment-service` (Phase 8).
- Redis Pub/Sub has no persistence: a notification published while no
  one is subscribed (browser tab closed) is simply lost. This is why
  `GET /alerts` — reading the actual table — is the system of record,
  and the WebSocket is additive, not load-bearing.
- The spend-spike and subscription-price-increase thresholds
  (3x / 90-day / min-5, and 5%) are reasonable starting points, not
  tuned against real usage data — revisit if they prove noisy or
  insensitive once real data exists to evaluate them against.

## Verification checklist

- [x] core-api: `pytest -v` — 108 tests passing (98 from Phases 1-8 + 10
      new): alerts list/mark-read/cross-user-isolation/unread-filter, WS
      ticket issuance/redemption/single-use/unknown-ticket
- [x] core-api: `alembic revision --autogenerate` produced a clean
      `alerts` table (two enum types this time — fixed the now-familiar
      Postgres-native-enum cleanup gap in `downgrade()` for both,
      proactively rather than after discovering it broken); full
      `upgrade → downgrade base → upgrade` cycle verified
- [x] anomaly-service: `pytest -v` — 17 tests passing, including the
      idempotency-on-redelivery test described above and a test proving
      one event can still raise multiple distinct alert types
- [x] notification-service: `pytest -v` — 5 tests passing (topic→type
      mapping, unrecognized-topic handling, actual Redis publish via
      fakeredis)
- [x] `ruff check .` — clean across core-api and all three new/touched
      packages
- [x] **Full pipeline verified against real Postgres, Redis, and
      Redpanda, running all four real processes simultaneously**
      (core-api, enrichment-service, anomaly-service,
      notification-service — not test doubles): registered a user,
      opened a real WebSocket connection using the ticket flow, created
      the same transaction twice, and received the resulting
      `duplicate_charge` alert **live over the actual WebSocket
      connection** within seconds — end to end through every hop of the
      pipeline this project's architecture claims to have. Confirmed the
      same alert persisted correctly via `GET /api/v1/alerts` afterward.
