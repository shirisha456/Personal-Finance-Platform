# ADR-0007: Service extraction boundaries — and the shared-database tradeoff that comes with them

## Status

Accepted

## Context

`enrichment-service` needs to read a transaction's merchant name and
write its resolved category back — data that lives in tables `core-api`
owns and migrates. Two ways to do that: call `core-api` over HTTP, or
connect directly to the same Postgres database and touch the specific
columns it needs. This decision also sets the pattern for Phase 9's
`anomaly-service` and `notification-service`, so it's worth deciding
once, explicitly, rather than re-deciding it per service.

## Decision

`enrichment-service` (and the services Phase 9 adds) connect directly to
the same Postgres database as `core-api`, using a **minimal, explicitly
declared column subset** (`app/db.py`'s bare `Table()` objects — no ORM
models, no import of `core-api`'s code) — never the full schema, never
running migrations, and never touching a column outside exactly what
that service needs. `core-api` remains the only thing that runs Alembic
against this database.

Everything that crosses a genuine process/deploy boundary — the actual
hand-off from one stage of the pipeline to the next — goes through
Kafka, not the database: `enrichment-service` doesn't call `core-api`'s
API to report the result, it publishes `transactions.enriched` and lets
whichever service cares (Phase 9's `anomaly-service`) consume it.

## Alternatives considered

- **Call `core-api`'s HTTP API instead of touching the database
  directly** — the more conventional service-boundary choice, and
  arguably the "purer" one. Rejected for this phase for a concrete
  reason: `core-api` doesn't have (and doesn't need, at this project's
  scope) service-to-service authentication or an internal API surface
  distinct from the user-facing one — building that just to let one
  background worker set a category_id is more new surface area than the
  problem justifies. If a second, third, and fourth service all needed
  to touch increasingly complex core-api state, that calculus would
  flip; for "read a merchant name, write a category_id," it doesn't yet.
- **Give `enrichment-service` its own database** — the strongest form of
  service independence, and the "correct" answer at real production
  scale. Rejected as disproportionate to a personal-scale project: it
  would mean either duplicating transaction data (with its own
  consistency problems) or making `enrichment-service` the source of
  truth for `category_id` and having `core-api` read it back somehow —
  more architecture than four Kafka-connected services processing a few
  events each justify.

## Consequences

- **Real coupling cost**: if `core-api` renames or drops a column
  `enrichment-service`'s `Table()` declaration references, nothing
  catches that at build time or migration time — it fails at runtime,
  the next time that code path runs. This is the honest cost of the
  shared-database choice, not something to gloss over. Mitigated
  partially by keeping the column subset small and by this service's own
  test suite exercising every query against a real (if minimal) schema.
- All four eventual services (`enrichment`, `anomaly`, `notification`,
  and whatever else) share this same database-access pattern
  consistently — no service quietly picked a different integration
  style.
- `core-api` remains the single owner of schema evolution — a
  Postgres-level permissions boundary (a read/write-scoped role for the
  consumer services, not the migration-running role) is real future
  hardening, not implemented at this phase's scope (all services
  currently connect with the same `personal_finance_platform` user for local-dev
  simplicity).

## Validation

`services/enrichment-service/tests/test_db.py` runs every `app/db.py`
query against a real (if minimal, hand-created) schema — not mocked —
so a column-name mismatch between this service's `Table()` declarations
and `core-api`'s actual Alembic-managed schema would fail loudly in this
service's own test suite. Phase 8's manual end-to-end verification
(`docs/phase8.md`) additionally runs this service against the real,
Alembic-migrated `core-api` database.
