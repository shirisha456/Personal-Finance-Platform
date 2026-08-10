# Phase 4 — Budgets, Goals, and Net Worth

## Goal

Per-category monthly budgets with a real budget-vs-actual computation,
savings goals, and net-worth snapshots — the first phase with genuine
cross-module read logic (budget-actual joins `budgets` against
`transactions` through `accounts`) and the first phase where response
caching actually matters (both computations are non-trivial aggregate
queries, not simple lookups).

## Architecture

```
PUT /api/v1/budgets                → upsert by (user, category, month)
                                      → invalidates budget_actual:{user}:*
GET /api/v1/budgets/actual?month=  → app/budgets/service.py::compute_budget_actual
                                      (LEFT JOIN transactions, grouped by category,
                                      net of refunds) → Redis-cached 60s

POST /api/v1/transactions          → invalidates budget_actual:{user}:*
                                      (the only write that actually changes
                                      what compute_budget_actual returns)

POST /api/v1/networth/recompute    → app/networth/service.py::recompute_snapshot
                                      (classified by account TYPE, not the sign
                                      of current_balance_minor) → upserts today's
                                      snapshot → invalidates networth:{user}:*
GET /api/v1/networth?days=         → Redis-cached 60s
```

`app/core/cache.py` is new this phase — the general-purpose Redis
cache-get/set/invalidate-prefix helpers, extracted out of
`app/core/idempotency.py` (which now just calls into it) rather than
reimplementing the same fail-open pattern a third time. Falls under
[ADR-0002](adr/0002-fail-open-redis-dependencies.md).

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Cache invalidation scope | Precise: only writes that actually change a cached computation's inputs invalidate it (transactions → budget_actual; recompute → networth) | Also invalidating `networth:{user}` on every transaction write — net worth is a point-in-time snapshot derived from account balances, not transactions, so that dependency doesn't actually exist |
| Net worth asset/liability split | Classified by `AccountType` (credit/loan → liability), magnitude via `abs()` | Trusting the sign of `current_balance_minor` — fragile if a caller enters a credit balance as a positive "amount I owe" (the common real-world mental model) instead of following the schema's negative-for-liability convention |
| Budget-actual "actual" calculation | Net of all transactions in the category+month (`-sum(amount_minor)`), so a refund offsets its original expense | Only counting negative (expense) transactions — would make a refunded purchase permanently count against the budget |
| Snapshot/budget "upsert" | `SELECT` then `UPDATE`-or-`INSERT` in application code | `INSERT ... ON CONFLICT DO UPDATE` — the ORM-level version is simpler to read and test identically against SQLite (unit tests) and Postgres (integration/CI); see Tradeoffs for the cost of this choice |
| Cache helper location | Shared `app/core/cache.py`, `idempotency.py` now built on top of it | Keeping idempotency's Redis logic separate — would have meant three independent copies of the same fail-open try/except by the time budget-actual and net-worth caching were added |

## Tradeoffs

- The application-level upsert (`SELECT` then branch) has a real, accepted
  race condition: two concurrent identical `PUT /budgets` (or
  `POST /networth/recompute`) calls for the same key could both miss the
  `SELECT` and both attempt an `INSERT`, and the second would fail against
  the unique constraint rather than silently succeeding as an update.
  This is judged acceptable for a single-user-driven personal finance app
  (there's no legitimate scenario with two concurrent identical writes
  from the same user) rather than adding `ON CONFLICT DO UPDATE` or a
  retry-on-`IntegrityError` wrapper for a race that essentially can't
  occur in practice.
- Budget-actual is scoped to categories that already have a budget set —
  a category with real spending but no budget doesn't appear in the
  response. This is a deliberate choice, not an oversight: the
  endpoint answers "how am I
  doing against my budgets," not "here's all my spending."

## Extensibility

Any future aggregate/expensive read that should be cached follows the
same three-line pattern: `cache_get_json` → compute on miss →
`cache_set_json`. Any future write that changes a cached computation's
inputs calls `cache_delete_prefix` with that computation's key prefix —
no new shared infrastructure needed.

## Verification checklist

- [x] `alembic revision --autogenerate` produced a clean `budgets` /
      `goals` / `net_worth_snapshots` migration, applied against real
      Postgres
- [x] `pytest -v` — 67 tests passing (55 from Phases 1-3 + 12 new):
      budget upsert-is-idempotent-on-the-row, list-by-month, the
      budget-actual computation itself (verified against a hand-computed
      expected value including a refund and an out-of-month transaction
      that must be excluded), cache invalidation on write, goals CRUD +
      cross-user isolation, net worth asset/liability classification
      (including a positive-entered credit balance), and recompute being
      an upsert for the same day
- [x] `ruff check .` — clean (fixed two genuine `DTZ011` naive-datetime
      findings by switching `date.today()` to `datetime.now(UTC).date()`,
      consistent with `TimestampMixin`'s UTC convention elsewhere)
- [x] End-to-end against real Postgres + real Redis: set a $500 budget,
      posted a $150 transaction in that category, confirmed
      `GET /budgets/actual` reflected the fresh $150 spend immediately
      (proving cache invalidation actually fires, not just that the code
      compiles), created a goal, recomputed net worth with one checking
      account ($2,000) and one credit account (balance entered as
      positive $150) — got `assets=200000, liabilities=15000,
      net_worth=185000` in minor units, confirming the type-based
      classification works regardless of sign convention
