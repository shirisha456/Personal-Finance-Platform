# Phase 5 — Investments and Market Data

## Goal

Manual holdings and a watchlist, get-or-create securities by symbol, and
an on-demand price refresh against an optional external market-data
provider — the same "degrades gracefully when unconfigured" contract
already established for other optional integrations.

## Scope note: what "market data" means in this phase

A fully-featured market-data integration could be a standalone
Kafka-consuming poller service (`market-data-service`) that runs on a
schedule and publishes `prices.updated` events. That service would
depend on infrastructure — the transactional outbox and event
contracts — that doesn't exist until Phase 7. Building a Kafka consumer
with nothing to consume yet would be exactly the kind of half-finished,
untestable scaffolding this project is trying to avoid.

So Phase 5 scopes "market data" to what's genuinely buildable and
testable now: a synchronous, on-demand `POST /investments/prices/refresh`
endpoint, behind the same provider-abstraction (`MarketDataProvider`)
a later scheduled poller can also use. Extracting that poller into
`services/market-data-service` once the event pipeline exists (Phase 7+)
is a real, planned piece of future work, not a gap being quietly
hidden — it's called out here and was in fact built later, without a
Kafka topic of its own (see [ADR-0014](adr/0014-market-data-service-no-kafka-topic.md)).

## Architecture

```
POST /investments/holdings, /investments/watchlist
  → app/investments/service.py::get_or_create_security (lookup-or-insert by symbol)

POST /investments/prices/refresh
  → app/investments/market_data.py::get_market_data_provider
      → unconfigured: raises MarketDataNotConfigured (503), handled automatically
        by the Phase 1 AppError envelope — no try/except needed at the call site
      → configured: TwelveDataProvider batches every relevant symbol into one HTTP call
  → updates Security.latest_price_minor / latest_price_at for whatever
    the provider actually returned a price for; a symbol the provider
    doesn't recognize is silently skipped, not a failure for the batch
```

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Market-data scope this phase | Synchronous, on-demand refresh endpoint | The full scheduled Kafka-poller microservice — depends on Phase 7 infrastructure that doesn't exist yet |
| Unconfigured-provider handling | `MarketDataNotConfigured` is itself an `AppError` subclass (503), so no per-route try/except is needed | A plain `RuntimeError` — would need an explicit try/except at every call site to catch and translate into the right HTTP status |
| No mock/fallback price | A security's price is `null` until a real provider successfully prices it — never a synthetic default | Returning a fake price when unconfigured — would be indistinguishable from real data in a response body |
| Security/holding relationships | No ORM `relationship()` between `Holding`/`Watchlist` and `Security` — responses are built by joining and passing both objects to a plain constructor function | SQLAlchemy `relationship()` + lazy attribute access — async SQLAlchemy requires either eager loading or an active greenlet context for lazy loads; explicit joins sidestep the whole class of `MissingGreenlet` bugs this could introduce |
| Symbol normalization | Uppercased server-side via a Pydantic validator | Trusting client-supplied casing — "aapl" and "AAPL" would otherwise create two different `Security` rows for the same stock |

## Tradeoffs

- `refresh_prices` re-fetches every symbol relevant to the current user
  on every call, with no cooldown/rate limiting of its own beyond
  whatever the underlying provider enforces. Acceptable for an
  on-demand, user-initiated action; the scheduled poller (Phase 8+) is
  where real rate-limit-aware batching across *all* users matters.
- Get-or-create-by-symbol means a typo'd symbol (e.g. "AAPLE") silently
  creates a new, permanently unpriced `Security` row rather than being
  rejected — there's no symbol-validity check against the provider at
  creation time (only at refresh time does an unrecognized symbol
  silently fail to price, not fail to exist).

## Verification checklist

- [x] `alembic revision --autogenerate` produced a clean `securities` /
      `security_prices` / `holdings` / `watchlist_items` migration,
      applied against real Postgres
- [x] `pytest -v` — 79 tests passing (67 from Phases 1-4 + 12 new):
      get-or-create-by-symbol (including case normalization and two
      holdings sharing one security row), holdings CRUD + cross-user
      isolation, watchlist add-is-idempotent (asserted via HTTP status:
      201 on first add, 200 on the repeat), refresh-prices 503 when
      unconfigured, and refresh-prices correctly updating priced symbols
      while leaving an unrecognized symbol's price `null` (via a fake
      provider override)
- [x] `ruff check .` — clean
- [x] End-to-end against real Postgres + real Redis, **with no
      `MARKET_DATA_API_KEY` set** (the real, honest default state):
      created a holding, added and re-added the same watchlist symbol
      (201 then 200, not duplicated), called `POST
      /investments/prices/refresh` and got a genuine 503 with the
      documented error envelope — not a crash, not a fake price
