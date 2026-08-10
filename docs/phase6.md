# Phase 6 — Plaid Integration

## Goal

Bank account linking via Plaid: link-token creation, public-token
exchange, an encrypted access-token store, and cursor-based transaction
sync — built to actually hold four correctness properties this kind of
pagination/status-tracking integration needs (see Design decisions).

## Architecture

```
POST /institutions/link-token → PlaidClient.create_link_token
POST /institutions             → service.link_institution
                                    → exchange_public_token
                                    → encrypt(access_token) → Institution row
                                    → sync_institution (inline, first sync)
POST /institutions/{id}/sync   → service.sync_institution
                                    → loops while has_more:
                                        sync_transactions(cursor)
                                        → upsert accounts (get-or-create by plaid_account_id)
                                        → upsert transactions (by account_id+external_id —
                                          the Phase 3 unique constraint)
                                        → delete removed transactions
                                    → on PlaidApiError: status = error, re-raise
                                    → on success: status = active, cursor persisted
DELETE /institutions/{id}      → service.unlink_institution
                                    → best-effort plaid_client.remove_item
                                    → always: status = revoked locally
```

`PlaidClient` is a `Protocol` (mirroring Phase 5's `MarketDataProvider`)
with one real implementation, `PlaidRestClient`, that calls Plaid's REST
API directly via `httpx.AsyncClient` — see the design decision below for
why this isn't the official `plaid-python` SDK.

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Plaid client implementation | Direct REST calls via `httpx.AsyncClient`, fully async | The official `plaid-python` SDK — its generated client is synchronous (blocking `urllib3`), which would silently violate ADR-0001's async-everywhere principle the moment a route called it directly |
| `has_more` pagination | `sync_institution` loops until `has_more` is `False`, processing every page | Processing only one page per sync call — a real, silent partial-sync bug on any institution with a large transaction backlog |
| `plaid_institution_id` | Nullable, populated from what the client (Plaid Link's `onSuccess` metadata) actually sends; `None` if not provided | Hardcoding the literal string `"unknown"` for every institution, always, even though the column exists to hold real data |
| Sync failure handling | `institution.status` transitions to `error` on a `PlaidApiError`, and back to `active` on the next successful sync | Never transitioning status away from `active` — leaves a status badge in the UI implying live tracking that isn't actually happening |
| Unlink | Calls Plaid's `/item/remove`, but the local revocation always succeeds even if that call fails (logged, not raised) | Never calling `/item/remove` at all — a "removed" institution would stay live on Plaid's side indefinitely |
| Transaction dedupe | Reuses Phase 3's `UniqueConstraint(account_id, external_id)` — a DB-level guarantee, not a new mechanism | Nothing new needed here — this is the payoff of the Phase 3 decision to add the constraint proactively before Plaid sync existed to populate `external_id` |

## Bugs found and fixed while building this phase

Two of these are pre-existing latent bugs, not something introduced this
phase — found because this phase was the first to actually exercise a
migration's `downgrade()` end-to-end (upgrade → downgrade to base →
re-upgrade), which nothing before this had done:

1. **Postgres native ENUM types outlive `DROP TABLE`.** Both this
   phase's `institution_status` migration and the already-committed
   Phase 3 migration's `account_type` enum had this gap — Alembic's
   autogenerate doesn't add a `DROP TYPE` when a table using a native
   enum is dropped, so a downgrade-then-upgrade cycle fails with
   `DuplicateObject`. Fixed in both migration files (`op.execute('DROP
   TYPE IF EXISTS ...')` in `downgrade()`); `upgrade()` — already
   applied — is untouched in both.
2. **An unnamed `UniqueConstraint`/`ForeignKey` breaks `downgrade()`.**
   `Account.plaid_account_id` used `unique=True` without `index=True`,
   creating an anonymous constraint Alembic could `create` but not later
   `drop` by name. Fixed by adding `index=True` (matching every other
   unique column in this app) and naming the new `institution_id`
   foreign key explicitly, since it's added via `ALTER TABLE` (not baked
   into `accounts`' original `CREATE TABLE`, which is why this exact
   issue never showed up before).

## Tradeoffs

- The first sync happens synchronously inside `POST /institutions`,
  blocking that request until Plaid's `/transactions/sync` responds
  (potentially several pages deep for an account with a lot of history).
  Accepted for this phase's scope; genuinely decoupling it is what the
  transactional outbox (Phase 7) and the event pipeline are for.
- Still no Plaid webhook receiver — sync is user-triggered
  (`POST /institutions/{id}/sync`) or happens once at link time, not
  pushed by Plaid. This is an honest, documented gap, not something
  silently deferred without comment: a webhook receiver is realistic
  future work, not attempted here because it needs
  a publicly reachable endpoint this local-dev-first project doesn't have
  yet.

## Verification checklist

- [x] `alembic revision --autogenerate` produced `institutions` +
      `accounts.institution_id`/`plaid_account_id`; caught and fixed the
      unnamed-constraint issue *before* applying, by regenerating after
      fixing the model
- [x] **Full migration reversibility proven**, not just assumed: `alembic
      upgrade head` → `alembic downgrade base` → `alembic upgrade head`
      against real Postgres, zero errors, final schema identical
- [x] `pytest -v` — 92 tests passing (79 from Phases 1-5 + 13 new):
      encryption round-trip + not-configured guard, link → account +
      transaction creation with the sign flip verified numerically,
      generic-name fallback without Link metadata, the `has_more`
      multi-page loop (verified via a queued two-page fake client),
      modified-transaction update-not-duplicate, sync failure setting
      `status=error`, unlink calling `remove_item` and revoking locally
      even when that call fails, and cross-user isolation on sync/unlink
- [x] `ruff check .` — clean
- [x] End-to-end against real Postgres + real Redis, **with no Plaid
      credentials configured** (the honest default): registered a user,
      got a genuine 503 from `/institutions/link-token`, confirmed
      `GET /institutions` returns `[]` without crashing, confirmed
      unrelated features (accounts, health) still work normally
