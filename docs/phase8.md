# Phase 8 — Transaction Enrichment

## Goal

The first standalone, independently deployable Kafka consumer:
`services/enrichment-service` categorizes newly ingested transactions
(rules engine, optional OpenAI fallback) and flags recurring merchants,
publishing `transactions.enriched` for Phase 9's `anomaly-service` to
consume.

## Architecture

```
transactions.ingested
  → app/consumer.py::process_message
      → categorize_by_rules (keyword match against a fixed 10-category taxonomy)
      → categorize_with_ai_fallback (only if rules found nothing; OpenAI
        gpt-4o-mini, 30-day Redis cache, returns None rather than
        guessing if unconfigured or the call fails)
      → app/db.py::get_category_id_by_name (resolves the name to the
        UUID apps/core-api's Phase 3 migration seeded)
      → count_prior_occurrences → is_recurring flag (>= 3rd occurrence)
      → app/db.py::set_transaction_category (UPDATE-by-id — naturally
        idempotent; see below)
  → transactions.enriched (published regardless of whether a category
    was actually found — a null category_id/category_name is a valid,
    meaningful result, not an error)
```

`app/db.py` is the same minimal-column-subset pattern described in
[ADR-0007](adr/0007-service-extraction-boundaries.md) — this service
connects directly to `core-api`'s Postgres database but owns no schema
and runs no migrations against it.

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| OpenAI client | Official `openai` SDK's `AsyncOpenAI` | A hand-rolled REST client (the approach Phase 6 took for Plaid) — unnecessary here since OpenAI's SDK genuinely is async, unlike `plaid-python`; ADR-0001 has no conflict to route around |
| Recurring-merchant detection | A simple count threshold (≥3rd occurrence of the same merchant name on the same account) | Nothing more sophisticated — this is a deliberately simple heuristic, not ML, and is documented as such; Phase 9's `anomaly-service` is the actual consumer of this signal and can evolve the definition later without touching this service |
| Health check | A minimal stdlib `http.server` on `/health`, its own thread, wired into a Docker `HEALTHCHECK` | No healthcheck at all — leaves Docker/Kubernetes with no way to tell this process is actually alive |
| Poison messages | Caught, logged, offset committed anyway — no dead-letter topic | Building a DLQ now — real infrastructure this phase's scope doesn't justify yet; documented as an accepted, known gap, not silently left unmentioned |

## Idempotency, concretely

`set_transaction_category` is a plain `UPDATE ... WHERE id = :id` — no
insert, no dedup table needed. Reprocessing the same
`transactions.ingested` message (a real possibility under the outbox's
at-least-once delivery, per ADR-0005) just sets the same `category_id`
again and republishes an equivalent `transactions.enriched` event. This
is the one Phase 8/9 service where "just make the write idempotent by
construction" is enough — Phase 9's `anomaly-service` needs a different
answer, because inserting a new alert row isn't naturally idempotent the
way updating a category is.

## Tradeoffs

- No dead-letter queue — a permanently malformed message is logged and
  skipped, not preserved for inspection. Accepted at this scope; a real
  DLQ is meaningful future work, not implemented here.
- `is_recurring` is recomputed from scratch (a full count query) on
  every message rather than incrementally maintained — fine at this
  data volume, would need revisiting if per-account transaction history
  grew into the millions.

## Verification checklist

- [x] `pytest -v` — 24 tests passing: rules-based categorization
      (parametrized across merchants, case-insensitivity, no-match),
      AI fallback (cache hit skips the API call entirely, rejects a
      category outside the taxonomy, fails closed to `None` on any
      OpenAI error), the full `process_message` flow against a real
      (if minimal) schema — categorization, leaving a genuinely
      unmatched merchant uncategorized, the recurring-merchant
      threshold, redelivery producing the same resulting state twice
      (not divergent), and gracefully skipping a transaction that no
      longer exists
- [x] `ruff check .` — clean
- [x] **Full pipeline verified against real Postgres + Redis + Redpanda**,
      running the actual `python -m app.main` consumer process (not a
      test double): created an uncategorized "Starbucks" transaction via
      the real `core-api`, confirmed `transactions.ingested` reached the
      real topic, ran the real `enrichment-service` process against it,
      confirmed the transaction's `category_id` in Postgres now points
      at the actual seeded "Food & Dining" category row, confirmed
      `transactions.enriched` landed on the real topic with the correct
      category, and confirmed `GET /health` on the service's own health
      port responded `ok`
