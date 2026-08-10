# Phase 3 — Accounts and Transactions

## Goal

Manual financial accounts and transactions, with real pagination,
idempotent transaction creation, and a shared ownership-check helper —
the first phase where the API surface is large enough that duplicated
authorization logic would actually be a risk.

## Architecture

```
POST /api/v1/accounts, /api/v1/transactions
  → app/core/ownership.py::get_owned (accounts: direct user_id column)
  → transactions/router.py::_get_owned_transaction (join through account —
    transactions have no user_id column of their own)

POST /api/v1/transactions
  → Idempotency-Key header → app/core/idempotency.py (Redis-backed,
    fails open per ADR-0002) → same key returns the cached response
    instead of inserting a second row

GET /api/v1/accounts, /api/v1/transactions
  → app/core/pagination.py::Pagination / Page[T] (limit/offset, capped at 100)
```

`categories` is introduced this phase too — `transactions.category_id`
references it, so the table has to exist before transactions do, even
though nothing populates it with real categorization until Phase 8
(enrichment-service). It's seeded by the migration itself with the same
fixed 11-category taxonomy used throughout this app.

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Ownership checks | One shared `app/core/ownership.py::get_owned` helper, used by every direct-`user_id` resource | Hand-rolled `_get_owned_*` per router — functionally fine but duplicated 8× with no shared guarantee a new module gets it right |
| Category seed data | `INSERT ... ON CONFLICT (name) DO NOTHING` with deterministic (`uuid5`) IDs | A `bulk_insert` of fresh `uuid4()` rows with no existence check — duplicates the taxonomy if the migration is ever re-run |
| Plaid dedupe key | `UniqueConstraint(account_id, external_id)` added to the schema now, even though nothing populates `external_id` until Phase 6 | Waiting until Phase 6 to add the constraint — leaves dedupe app-level-only and non-atomic in the meantime, with no DB-level guarantee it's actually account-scoped |
| Redis introduction | Brought in this phase specifically for idempotency-key caching, fail-open (see [ADR-0002](adr/0002-fail-open-redis-dependencies.md)) | Deferring Redis to a later phase — but "Idempotency" is explicit Phase 3 scope, and idempotency-key caching is the natural first real use |
| Pagination | Generic `Page[T]` (PEP 695 syntax) + `limit`/`offset`, capped at 100 | No pagination — every list endpoint returned the full result set |
| Accounts schema | Manual-account fields only (`name`, `type`, `currency`, `current_balance_minor`) | Including `institution_id`/`plaid_account_id` now, nullable, with no `institutions` table yet to reference — Phase 6 adds those via its own migration once the table they'd point at actually exists |

## Tradeoffs

- Idempotency-key protection is scoped per-user in the Redis key
  (`idempotency:{user_id}:{key}`) rather than global — a deliberate,
  cheap correctness improvement over a plausible-but-unverified original
  design, at the cost of one extra UUID segment in the cache key.
- `get_owned` centralizes the *check*, but each router still needs a
  thin 2-3 line wrapper to satisfy FastAPI's path-parameter-name-based
  dependency injection (a fully generic `Depends`-based version isn't
  possible without either fixing the path param name globally or losing
  type safety) — an accepted middle ground, not full deduplication.
- Transactions still have no `user_id` column — ownership is always
  derived by joining through `accounts`. This is a deliberate
  normalization choice (an
  account's owner is the single source of truth for who owns its
  transactions), not an oversight.

## Extensibility

Any future direct-`user_id` resource (goals, budgets, institutions,
alerts, insights, net worth snapshots in later phases) reuses
`app/core/ownership.py::get_owned` and `app/core/pagination.py::Page`
directly — no new shared infrastructure needed until a resource shows up
that isn't owned by a simple `user_id` column.

## Verification checklist

- [x] `alembic revision --autogenerate` produced a clean `categories` +
      `accounts` + `transactions` migration; hand-edited to add the
      idempotent seed insert, applied cleanly against real Postgres
- [x] Verified the seed insert's idempotency directly: re-running the
      `INSERT ... ON CONFLICT` by hand reports `INSERT 0 0` and the
      category count stays at 11
- [x] `pytest -v` — 55 tests passing (35 from Phases 1-2 + 20 new):
      accounts/transactions CRUD, pagination, merchant search and
      date-range filters, cross-user isolation on both accounts and
      transactions (including creating a transaction on someone else's
      account), idempotency (same key → same transaction, different keys
      → separate transactions, and fails open when Redis is unreachable)
- [x] `ruff check .` — clean
- [x] End-to-end against real Postgres **and** real Redis (not fakeredis):
      registered a user, listed all 11 seeded categories, created an
      account, POSTed the same transaction twice with the same
      `Idempotency-Key` — got the same transaction ID back both times,
      and `GET /transactions` confirmed only one row exists
