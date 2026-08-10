# Meridian

A cloud-native personal finance and investment platform: accounts and
transactions, Plaid bank sync, budgets and savings goals, investment
tracking, an event-driven pipeline (Kafka/Redpanda) that categorizes
transactions and detects anomalies in real time, AI-generated monthly
insights, and full observability across the async pipeline.

This repository was built in 16 incremental, reviewed phases — see
[Development phases](#development-phases) below, all complete. Every
phase's design decisions live in its own doc under `docs/`, and every
architectural decision worth remembering has an ADR under
[`docs/adr/`](docs/adr/). Start with
[`docs/case-study.md`](docs/case-study.md) for the short version, or
[`docs/architecture.md`](docs/architecture.md) for the system diagrams.

## Problem this solves

Personal finance data is scattered across banks, brokerages, and manual
tracking, and turning it into a single accurate picture — accounts,
spending by category, budget adherence, net worth, investment performance —
usually means either a spreadsheet or trusting a third party with
read access to every account. Meridian centralizes that view behind
authentication you control, syncs transactions automatically where
possible (Plaid), and processes them asynchronously so categorization
and anomaly detection don't block the request that created them.

## Key engineering highlights

- Rotating refresh tokens with **theft/reuse detection**: a refresh token
  presented twice (already used or already revoked) kills its entire
  token family, not just itself — see
  [ADR discussion in docs/phase2.md](docs/phase2.md)
- Async SQLAlchemy 2.0 end-to-end, not sync calls threadpooled under
  async routes — see [ADR-0001](docs/adr/0001-async-sqlalchemy.md)
- One consistent error envelope for every failure path (deliberate,
  validation, routing, and unhandled exceptions alike) — see
  [`docs/phase1.md`](docs/phase1.md)
- A per-account, DB-level unique constraint backing transaction dedupe —
  a race-safe guarantee an app-level-only check under concurrent syncs
  can't give you — see [`docs/phase3.md`](docs/phase3.md)
- Idempotency-key protected transaction creation, Redis-backed, fails
  open on a Redis outage rather than blocking writes — see
  [ADR-0002](docs/adr/0002-fail-open-redis-dependencies.md)
- Cache invalidation scoped to what a write actually affects, not
  blanket invalidation of every plausibly-related key — see
  [`docs/phase4.md`](docs/phase4.md)
- Optional external integrations (market data, Plaid) fail as a typed
  503 via the shared error model, never a mock/synthetic value standing
  in for real data — see [`docs/phase5.md`](docs/phase5.md)
- Plaid access tokens encrypted at rest (Fernet, a documented KMS
  stand-in — [ADR-0003](docs/adr/0003-local-envelope-encryption-stand-in.md));
  a real REST client instead of the official SDK specifically because
  that SDK is synchronous and would violate ADR-0001; a `has_more` sync
  loop and a `status=error` transition for when a sync fails outright —
  see [`docs/phase6.md`](docs/phase6.md)
- Proved migration reversibility for real (`upgrade` → `downgrade base`
  → `upgrade`, not just written and assumed to work) — caught and fixed
  a Postgres-native-enum cleanup bug this way, in both a new and an
  already-committed migration — see [`docs/phase6.md`](docs/phase6.md)
- Transactional outbox with the publish-then-mark ordering bug fixed by
  construction (a row is only marked published *after* Kafka confirms
  delivery), and a Kafka client choice (`aiokafka`, not the sync
  `confluent-kafka` SDK) that keeps the whole app async, not just the
  HTTP layer — see [ADR-0005](docs/adr/0005-transactional-outbox.md),
  [ADR-0006](docs/adr/0006-async-kafka-client.md); verified against a
  real Redpanda broker, not just a fake producer — see
  [`docs/phase7.md`](docs/phase7.md)
- The first independently deployable consumer service
  (`enrichment-service`), with its own minimal-column-subset database
  contract against a schema it doesn't own — see
  [ADR-0007](docs/adr/0007-service-extraction-boundaries.md); verified
  end-to-end running the real consumer process against real Postgres,
  Redis, and Redpanda, not just a fake producer — see
  [`docs/phase8.md`](docs/phase8.md)
- `anomaly-service` is provably idempotent, not just documented as such:
  a real `UNIQUE (source_event_id, alert_type)` constraint backs it —
  proven with a test that simulates message redelivery and asserts no
  duplicate alert is created — see [`docs/phase9.md`](docs/phase9.md)
- A short-lived, single-use WebSocket ticket instead of putting the
  long-lived access token in the connection URL — verified with a real
  browser-shaped WebSocket client receiving a live alert end-to-end
  through all four running services — see
  [`docs/phase9.md`](docs/phase9.md)
- Grounded LLM summaries, not free-form generation: the model only ever
  sees pre-computed spend aggregates, never raw transaction rows, and a
  deterministic template fallback keeps the feature available even when
  the LLM call fails or isn't configured — see
  [ADR-0008](docs/adr/0008-grounded-insight-generation-with-fallback.md);
  verified end-to-end including live delivery over the same WebSocket
  pipeline Phase 9 built — see [`docs/phase10.md`](docs/phase10.md)
- The frontend was built directly against the real, running backend API
  before a line of UI code was written, catching real mismatches early:
  bare-array assumptions that would crash against the backend's real
  `Page[T]` responses, a request field name that didn't match the
  schema, and a hardcoded WebSocket auth pattern the ticket-based flow
  (Phase 9) was specifically designed to close off — see
  [`docs/phase11.md`](docs/phase11.md)
- Two real bugs caught and fixed during frontend verification, not
  glossed over: a session that silently dropped on every page reload,
  and closed dialogs/selects that never actually unmounted — see
  [ADR-0009](docs/adr/0009-no-popup-close-animations.md) and
  [`docs/phase11.md`](docs/phase11.md)
- The entire ingest → enrich → detect-anomalies → notify pipeline
  traces as one connected request in Tempo, not four separate ones — a
  W3C traceparent is hand-carried through the outbox and Kafka message
  headers at every hop, since Kafka has no built-in trace propagation
  the way HTTP middleware does. Every structured JSON log line also
  carries the active `trace_id`, so Grafana's trace-to-logs link has
  something real to correlate against — verified with real trace and
  log data pulled directly from Tempo's and Loki's own APIs, not just
  code review — see [`docs/phase12.md`](docs/phase12.md)
- Two chaos tests proving this architecture's resilience claims against
  the real running stack, not just by reasoning about the code: killing
  `enrichment-service` mid-pipeline loses no data (Kafka consumer-group
  offsets pick up exactly where it left off), and stopping the Kafka
  broker itself doesn't touch the request path at all — a transaction
  still gets created in well under a second with Redpanda down, since
  the transactional outbox (ADR-0005) never makes a synchronous Kafka
  call — see [`docs/phase13.md`](docs/phase13.md)
- Real infrastructure-as-code (two Terraform environments, two Helm
  charts) with a CI job that actually validates it on every push,
  paired with an explicit, permanent record of what "validated" does
  and doesn't mean here: never applied against real AWS, by design, not
  by oversight. The one instance size this project actually recommends
  deploying to was chosen from real `docker stats` measurements of its
  own stack, not a guess — see
  [ADR-0011](docs/adr/0011-terraform-written-not-applied.md),
  [ADR-0012](docs/adr/0012-single-ec2-instance-sizing.md), and
  [`docs/phase14.md`](docs/phase14.md)

_More added as each phase lands — see the phase table below for what's
actually implemented today versus planned._

## Major features

- Email/password registration and login, Argon2id-hashed, JWT access
  tokens + rotating refresh tokens (Phase 2)
- Manual financial accounts and transactions: paginated listing, merchant
  search, date-range filtering, idempotent creation, cross-user isolation
  (Phase 3)
- Per-category monthly budgets with a real budget-vs-actual computation
  (net of refunds, Redis-cached), savings goals, and net-worth snapshots
  with type-based asset/liability classification (Phase 4)
- Investment holdings and a watchlist (get-or-create by ticker symbol),
  with an on-demand price refresh against an optional market-data
  provider (Phase 5)
- Plaid bank linking: link-token creation, public-token exchange,
  encrypted access-token storage, cursor-based transaction sync with
  full `has_more` pagination (Phase 6)
- Transactional outbox + a real Kafka event pipeline: every
  uncategorized transaction publishes a `transactions.ingested` event,
  verified landing on a real Redpanda topic (Phase 7)
- Automatic transaction categorization (rules engine + optional OpenAI
  fallback) and recurring-merchant detection, running as a standalone
  Kafka consumer service (Phase 8)
- Real-time anomaly detection (duplicate charges, spend spikes,
  subscription price increases) with idempotent alert creation, and
  live push to the browser over a ticket-authenticated WebSocket
  (Phase 9)
- Grounded monthly spending insights: real aggregation query, optional
  OpenAI summary with a deterministic template fallback, published as an
  event and delivered live over the existing WebSocket pipeline
  (Phase 10)
- A Next.js dashboard covering every backend feature above — accounts
  (with edit/delete and a currency selector), transactions (search,
  filtering, pagination, delete), budgets (with a real month picker),
  goals (create/edit/delete), investments (holdings, watchlist, price
  refresh), net worth, Plaid linking, and live alerts/insights over the
  ticket-authenticated WebSocket (Phase 11)
- Distributed tracing (OpenTelemetry → Tempo), metrics (Prometheus,
  scraped from all backend services), and structured JSON logs
  (Loki via Promtail), pre-wired in Grafana with a working dashboard and
  a functional trace-to-logs correlation (Phase 12)
- Per-IP rate limiting on `/login` and `/register`, Redis-backed and
  fail-open on a Redis outage — see
  [ADR-0013](docs/adr/0013-per-ip-fixed-window-rate-limiting.md)
- Cash-flow forecasting: projects the checking/savings/cash balance
  forward from real recurring-transaction patterns detected directly in
  Postgres (no ML, no guessing) — a merchant needs 3+ real occurrences
  before it's projected forward at all
- `market-data-service`: a scheduled poller pricing every tracked
  security independently of the on-demand refresh endpoint, deliberately
  built without a Kafka topic since nothing consumes one yet — see
  [ADR-0014](docs/adr/0014-market-data-service-no-kafka-topic.md)
- Symbol search for adding investment holdings: search a company name or
  ticker against a real market-data provider and pick from actual
  matches, rather than free-typing a symbol with no validation

_More added as each phase lands._

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for system diagrams
(overall system, the event-pipeline sequence, auth flow, observability
data flow, and both deployment topologies), and the per-phase docs
linked below for how each piece was built.

## Technology stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2 (async), Alembic, PostgreSQL, Redis |
| Event pipeline | Redpanda (Kafka API), shared Pydantic event contracts, transactional outbox, independently deployable consumers |
| Frontend | Next.js, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, Zustand |
| Security | Argon2id, JWT access tokens, rotating refresh tokens, HTTP-only cookies |
| Observability | OpenTelemetry, Prometheus, Grafana, Loki, Promtail, Tempo |
| Infrastructure | Docker, Docker Compose, GitHub Actions, Terraform, Helm |

## Repository structure

```
meridian/
├── .github/workflows/     CI
├── apps/core-api/         FastAPI backend — config, async DB, errors, health (Phase 1); domain modules added Phase 2+
├── services/
│   ├── enrichment-service/   Categorizes transactions.ingested → transactions.enriched (Phase 8)
│   ├── anomaly-service/       Detects anomalies → alerts.raised, idempotent (Phase 9)
│   ├── notification-service/  Fans out alerts.raised/insights.generated → Redis pub/sub (Phase 9)
│   └── market-data-service/   Scheduled poller pricing every tracked security, no Kafka topic (ADR-0014)
├── libs/events/             Shared event contracts — a real installable package (Phase 7)
├── web/                    Next.js dashboard — TanStack Query, Zustand, Base UI (Phase 11)
├── docs/                   Architecture docs, case study, ADRs, per-phase notes
├── observability/          Prometheus/Grafana/Loki/Promtail/Tempo config (Phase 12)
├── infra/                  Terraform + Helm — written and validated, never applied (Phase 14)
├── chaos/                  Chaos/recovery tests (Phase 13)
├── deploy/                 Production compose, nginx, backup/restore scripts (Phase 14)
├── docker-compose.yml      Local stack (infra-only until later phases add services)
└── README.md
```

## Development phases

| Phase | Scope | Status | Commit |
|---|---|---|---|
| 0 | Repository foundation | Complete | `chore: initialize Meridian monorepo and development tooling` |
| 1 | Core API and persistence | Complete | `feat: establish core API and PostgreSQL persistence` |
| 2 | Authentication and security | Complete | `feat: add secure authentication and rotating sessions` |
| 3 | Accounts and transactions | Complete | `feat: add accounts and idempotent transaction management` |
| 4 | Budgets, goals, and net worth | Complete | `feat: add budgeting goals and net worth tracking` |
| 5 | Investments and market data | Complete | `feat: add investment portfolio and market data tracking` |
| 6 | Plaid integration | Complete | `feat: integrate Plaid account linking and transaction sync` |
| 7 | Transactional outbox and events | Complete | `feat: introduce transactional outbox and Kafka event contracts` |
| 8 | Transaction enrichment | Complete | `feat: add asynchronous transaction enrichment pipeline` |
| 9 | Anomaly detection and notifications | Complete | `feat: add anomaly detection and real-time alerts` |
| 10 | AI financial insights | Complete | `feat: add grounded monthly financial insights` |
| 11 | Frontend | Complete | `feat: add Next.js dashboard frontend` |
| 12 | Observability | Complete | `feat: add tracing metrics and log aggregation` |
| 13 | Resilience and chaos testing | Complete | `feat: add chaos testing and resilience validation` |
| 14 | Infrastructure and CI/CD | Complete | `feat: add infrastructure as code and deployment automation` |
| 15 | Portfolio documentation | Complete | `docs: add architecture, API, security, and demo documentation` |

Each phase's design decisions and verification checklist:
[`docs/phase0.md`](docs/phase0.md), [`docs/phase1.md`](docs/phase1.md),
[`docs/phase2.md`](docs/phase2.md), [`docs/phase3.md`](docs/phase3.md),
[`docs/phase4.md`](docs/phase4.md), [`docs/phase5.md`](docs/phase5.md),
[`docs/phase6.md`](docs/phase6.md), [`docs/phase7.md`](docs/phase7.md),
[`docs/phase8.md`](docs/phase8.md), [`docs/phase9.md`](docs/phase9.md),
[`docs/phase10.md`](docs/phase10.md), [`docs/phase11.md`](docs/phase11.md),
[`docs/phase12.md`](docs/phase12.md), [`docs/phase13.md`](docs/phase13.md),
[`docs/phase14.md`](docs/phase14.md), [`docs/phase15.md`](docs/phase15.md).

### Post-phase-15 additions

Real gaps closed after the phase-15 documentation pass — each one was
either a self-documented known gap or an explicitly deferred piece of
the original scope, not something newly discovered by accident:

- **Rate limiting on `/login`/`/register`** — was listed under
  `docs/security.md`'s "known gaps" since Phase 2; closed with a
  Redis-backed, fail-open, per-IP limiter. See
  [ADR-0013](docs/adr/0013-per-ip-fixed-window-rate-limiting.md).
- **Cash-flow forecasting** — projects the checking/savings/cash balance
  forward from real recurring-transaction patterns. No dedicated ADR
  (no architectural tradeoff worth recording beyond what's already in
  `apps/core-api/app/forecast/service.py`'s own docstrings); see
  `apps/core-api/tests/test_forecast.py` for the verified behavior.
- **`market-data-service`** — the standalone poller `docs/phase5.md`
  and `docs/phase7.md` both flagged as real future work once the event
  pipeline existed. Built as a scheduled poller, deliberately without a
  Kafka topic — see
  [ADR-0014](docs/adr/0014-market-data-service-no-kafka-topic.md).
- **Symbol search for holdings** — replaced free-typed, unvalidated
  ticker entry with a real search against the market-data provider;
  manual entry is still available but is now an explicit, clearly-labeled
  opt-in rather than the default path.

## Local development setup

```bash
cd apps/core-api
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
pip install -e ../../libs/events   # shared event contracts — see docs/phase7.md
pip install -e ".[dev]"
cp .env.example .env

docker compose up -d postgres redis redpanda redpanda-topics   # or `docker compose up -d` for everything
alembic upgrade head                  # users, refresh_tokens, categories (seeded), accounts,
                                       # transactions, budgets, goals, net_worth_snapshots,
                                       # securities, security_prices, holdings, watchlist_items,
                                       # institutions, outbox_events, alerts, insights
uvicorn app.main:app --reload
```

`GET http://localhost:8000/live`, `/ready`, `/health`, and
`POST http://localhost:8000/api/v1/auth/register` should all respond.

```bash
cd web
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

`http://localhost:3000` should redirect to `/login`; register a user and
the dashboard should load real data from the backend above.

## Environment variables

- Root `.env.example` — compose-level substitution only (`OPENAI_API_KEY`,
  `MARKET_DATA_API_KEY`)
- `apps/core-api/.env.example` — `DATABASE_URL`, `ENVIRONMENT`, `LOG_LEVEL`,
  `CORS_ORIGINS`, `JWT_SECRET` (must be overridden outside development —
  see `Settings.assert_safe_for_environment`), `JWT_ALGORITHM`,
  `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`,
  `REDIS_URL`, `MARKET_DATA_API_KEY`/`MARKET_DATA_BASE_URL` (optional —
  see [`docs/phase5.md`](docs/phase5.md)), `PLAID_CLIENT_ID`/
  `PLAID_SECRET`/`PLAID_ENV` (optional), `ENCRYPTION_KEY` (required only
  once an institution is actually linked — see
  [ADR-0003](docs/adr/0003-local-envelope-encryption-stand-in.md)),
  `KAFKA_BOOTSTRAP_SERVERS` (defaults to `localhost:19092`, matching
  `docker-compose.yml`'s Redpanda port). Grows as later phases add
  OpenAI configuration.

## Database migrations

Alembic is wired up (`apps/core-api/alembic/`), running against a sync
driver (`psycopg`) independent of the app's async runtime driver — see
[ADR-0001](docs/adr/0001-async-sqlalchemy.md). Migrations so far:
`users`/`refresh_tokens` (Phase 2), `categories` (seeded, idempotently —
see [`docs/phase3.md`](docs/phase3.md)) / `accounts` / `transactions`
(Phase 3), `budgets` / `goals` / `net_worth_snapshots` (Phase 4),
`securities` / `security_prices` / `holdings` / `watchlist_items`
(Phase 5), `institutions` / `accounts.institution_id` /
`accounts.plaid_account_id` (Phase 6), `outbox_events` (Phase 7),
`alerts` (Phase 9), `insights` (Phase 10),
`outbox_events.trace_headers` (Phase 12, distributed trace propagation
through Kafka — see [ADR-0010](docs/adr/0010-direct-otlp-export-no-collector.md)).
Full reversibility (`downgrade` all the way to base,
then `upgrade` back to head) is verified, not just assumed, for every
migration — see [`docs/phase6.md`](docs/phase6.md) for a real bug this
caught.

```bash
cd apps/core-api
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## Backend test commands

```bash
cd apps/core-api
pytest -v          # 132 tests: health, errors, config, auth/security, rate limiting,
                   # accounts, transactions, idempotency, budgets, goals, net worth,
                   # investments, symbol search, forecast, encryption, institutions,
                   # outbox, alerts, ws-tickets, insights
ruff check .

cd ../../libs/events
pytest -v          # 6 tests: event contract shapes, versioning, Topics
ruff check .

cd ../../services/enrichment-service
pytest -v          # 24 tests: rules categorization, AI fallback, db access, full consumer flow
ruff check .

cd ../../services/anomaly-service
pytest -v          # 17 tests: 3 detection rules, idempotent alert creation, multi-alert-per-event
ruff check .

cd ../../services/notification-service
pytest -v          # 5 tests: topic-to-notification-type mapping, Redis pub/sub fan-out
ruff check .

cd ../../services/market-data-service
pytest -v          # 11 tests: symbol tracking, provider search/pricing, batch-failure isolation
ruff check .
```

## Frontend commands

```bash
cd web
npm run dev         # local dev server, http://localhost:3000
npm run build        # production build — typechecks and lints as part of the build
npm run typecheck    # tsc --noEmit
npm run lint          # eslint .
```

No automated test suite yet — see [`docs/phase11.md`](docs/phase11.md);
verification for this phase was manual, against the real backend and a
real browser.

## Docker Compose instructions

`docker-compose.yml` (project name pinned explicitly — see
[`docs/phase0.md`](docs/phase0.md)) starts Postgres (`localhost:5433`),
Redis (`localhost:6380`), Redpanda (`localhost:19092`), a one-shot
`redpanda-topics` job that creates every topic in
`meridian_events.Topics`, `core-api` (`localhost:8000`, depends on
Postgres/Redis being healthy and `redpanda-topics` completing
successfully; `alembic upgrade head` runs automatically on container
start), `enrichment-service`, `anomaly-service`, `notification-service`
(each exposes its `/health` + `/metrics` port — 8080/8081/8082
respectively — inside the container network only, not published to the
host), `market-data-service` (`:8083`, a scheduled poller rather than a
Kafka consumer — see [ADR-0014](docs/adr/0014-market-data-service-no-kafka-topic.md)),
and the observability stack below.

## Observability instructions

```bash
docker compose up -d tempo prometheus loki promtail grafana
```

- **Grafana** — `http://localhost:3001` (anonymous Admin access, no
  login — a local-dev-only setting, see
  [`docs/phase12.md`](docs/phase12.md)). The "Meridian — Pipeline
  Overview" dashboard is provisioned automatically; Prometheus, Loki,
  and Tempo are pre-wired as datasources.
- **Prometheus** — `http://localhost:9090`; scrapes `core-api:8000` and
  each worker's health-server port.
- **Tempo** — `http://localhost:3200` (query API); its OTLP/http
  receiver (`:4318`) is also published to the host so a service run
  outside Docker can still export traces.
- **Loki** — no host port published; reachable only via Grafana's
  proxied datasource or from another container on the compose network.

Tracing is opt-in per service via `OTEL_EXPORTER_OTLP_ENDPOINT`
(pre-set to `http://tempo:4318` for all app services in
`docker-compose.yml`; empty by default outside Docker, so a plain
`pytest` run or a bare `uvicorn --reload` never depends on Tempo being
up). See [`docs/phase12.md`](docs/phase12.md) and
[ADR-0010](docs/adr/0010-direct-otlp-export-no-collector.md) for the
full design, and that same doc's verification checklist for a real,
traced request walked end-to-end through Tempo and Loki.

## External integration behavior

Market data (Twelve Data, optional — [`docs/phase5.md`](docs/phase5.md)):
without `MARKET_DATA_API_KEY`, `POST /investments/prices/refresh` returns
a 503 and holdings/watchlist entries simply keep `latest_price_minor:
null`, "no price yet" — never a mock/synthetic price. Symbol search
degrades the same way: a 503 with a clear message and an explicit
manual-entry fallback in the UI, never a fake match.

Plaid (optional — [`docs/phase6.md`](docs/phase6.md)): without
`PLAID_CLIENT_ID`/`PLAID_SECRET`, `/institutions/link-token` and
`POST /institutions` return 503; `GET /institutions` returns `[]` rather
than erroring. When configured, sync is user-triggered (manual or at
link time) — there's no webhook receiver yet, a documented gap, not a
silent one.

OpenAI (optional — used in two independent places, each with its own
fallback): in `enrichment-service` ([`docs/phase8.md`](docs/phase8.md)),
without `OPENAI_API_KEY` categorization falls back to the rules engine
only — a merchant the rules don't recognize is simply left uncategorized,
never guessed. In `core-api`'s insights feature
([`docs/phase10.md`](docs/phase10.md),
[ADR-0008](docs/adr/0008-grounded-insight-generation-with-fallback.md)),
without `OPENAI_API_KEY` (or on any call failure) `POST /insights/generate`
falls back to a deterministic template summary computed from the same
real aggregates the AI prompt would have used — never a mock number.

## Security highlights

Argon2id password hashing (explicit, OWASP-cited parameters — not library
defaults), JWT access tokens (15 min), rotating refresh tokens with
theft/reuse detection (a replayed already-used-or-revoked token kills its
entire token family), refresh tokens stored only as a SHA-256 hash,
HttpOnly/SameSite=lax cookies scoped to `/api/v1/auth`, a startup guard
that refuses to boot in production with the placeholder JWT secret,
per-IP rate limiting on `/login`/`/register` (Redis-backed, fail-open —
[ADR-0013](docs/adr/0013-per-ip-fixed-window-rate-limiting.md)), and
Plaid access tokens encrypted at rest (Fernet, a documented KMS
stand-in — [ADR-0003](docs/adr/0003-local-envelope-encryption-stand-in.md)).
Documented fully in [`docs/security.md`](docs/security.md) (Phase 15).

## CI/CD summary

`.github/workflows/ci.yml` runs jobs on every push to `main` and every
pull request: `events-lib` (installs and tests `libs/events` on its
own), `backend` (installs `libs/events` then `apps/core-api`, runs
`alembic upgrade head` against a real Postgres service container, runs
`pytest`, runs `ruff check .`), `enrichment-service`,
`anomaly-service`, `notification-service`, `market-data-service` (each
installs its own dependencies, runs its own test suite and lint), and
`frontend` (`npm ci`, `tsc --noEmit`, `eslint .`, `npm run build` — no
test suite yet; see [`docs/phase11.md`](docs/phase11.md)). None of these
jobs spin up a real Redpanda service container — the outbox/Kafka tests
all use a fake producer/consumer; the real-broker round-trip (including,
from Phase 9, all four Kafka-aware services running simultaneously
against real Postgres/Redis/Redpanda) is verified manually each phase
touching it (see [`docs/phase7.md`](docs/phase7.md) onward). A further
job, `chaos-smoke-test` (gated to pushes on `main`, since it needs a
full `docker compose up --build` and deliberately kills/restarts
containers — too slow for every PR), runs both scripts under `chaos/`
against the real stack — see [`docs/phase13.md`](docs/phase13.md). An
`infra-validate` job runs `terraform fmt -check`/`validate` against
every module and both environments under `infra/terraform/`, and
`helm lint`/`template` against both charts (including all three
`worker` per-service values files) — genuine static validation catching
real config errors before they'd ever reach a deploy; see
[`docs/phase14.md`](docs/phase14.md) and
[ADR-0011](docs/adr/0011-terraform-written-not-applied.md) for exactly
what that validation does and doesn't prove.

## Infrastructure summary

**Written and statically validated, never applied to real cloud
infrastructure** — see [ADR-0011](docs/adr/0011-terraform-written-not-applied.md)
for the full, explicit accounting of what that does and doesn't prove.

- `infra/terraform/envs/dev` — VPC, EKS (managed node group + IRSA),
  RDS Postgres, ElastiCache Redis, and an S3 archival bucket. The "how a
  real team would run this on Kubernetes" design; never `apply`'d.
- `infra/terraform/envs/single-ec2` — one EC2 instance (`t3.large`,
  sized from real `docker stats` measurements — see
  [ADR-0012](docs/adr/0012-single-ec2-instance-sizing.md)), SSM-only
  access (no SSH key, no open port 22), an AWS Budgets tripwire, and
  optional Route 53. **This is the environment actually meant to be
  applied** — see [`deploy/README.md`](deploy/README.md) for the full
  runbook (first deploy, HTTPS via Let's Encrypt, backups, rollback,
  total-loss recovery, teardown).
- `infra/helm/core-api` and `infra/helm/worker` — Helm charts for the
  `dev` EKS environment; `worker` is one chart reused via a
  `values-<service>.yaml` file per consumer service, with KEDA
  Kafka-lag-based autoscaling for enrichment-service/anomaly-service and
  a fixed replica count for notification-service.
- `deploy/docker-compose.prod.yml` — the production compose file for the
  single-EC2 path: only nginx exposes host ports, every service has a
  memory/CPU limit derived from real measurements, and secrets come from
  a hand-created `/opt/meridian/secrets.env` (see
  `deploy/secrets.env.example`), never from Terraform state or a
  committed file.

See [`docs/phase14.md`](docs/phase14.md) for the full verification
checklist — every `terraform validate`/`fmt`, `helm lint`/`template`
command that was actually run, and its result.

## Known limitations

The insights feature's
AI summary path (`ai_summary()`) has not been exercised against the real
OpenAI API in this project — no key has been configured in any test or
local environment used so far; only the deterministic template fallback
has been verified end-to-end (see [`docs/phase10.md`](docs/phase10.md)).
No dead-letter queue for any of the three Kafka-consuming
services' message processing — a permanently malformed message is
logged and skipped, an accepted, documented gap (see
[`docs/phase8.md`](docs/phase8.md), [`docs/phase9.md`](docs/phase9.md)).
Redis Pub/Sub notifications have no persistence — a live alert missed
while disconnected isn't redelivered over the WebSocket, though it's
still visible via `GET /api/v1/alerts` (the system of record). No Plaid
webhook receiver — sync is user-triggered only (see
[`docs/phase6.md`](docs/phase6.md)). The budgets-goals-networth upsert
operations have a documented, accepted race condition under truly
concurrent identical requests (see [`docs/phase4.md`](docs/phase4.md)) —
not a concern for this app's single-user-driven write pattern. The
frontend has no automated test suite yet and no CSS open/close
animations on any dialog/popover/select — a deliberate tradeoff, not an
oversight, made after those animations turned out to break popups
actually closing; see
[ADR-0009](docs/adr/0009-no-popup-close-animations.md). Money is
formatted in a fixed `en-US` locale regardless of an account's actual
currency — correct for USD, cosmetically off for others (see
[`docs/phase11.md`](docs/phase11.md)). No OpenTelemetry Collector, no
Prometheus alerting rules, and Grafana runs with anonymous Admin access
— all deliberate, local-dev-scoped tradeoffs, not oversights; see
[`docs/phase12.md`](docs/phase12.md) and
[ADR-0010](docs/adr/0010-direct-otlp-export-no-collector.md).

## Future enhancements

The full list with rationale is in
[`docs/case-study.md`](docs/case-study.md#whats-next); in short:

- A dead-letter topic for the three Kafka consumers (currently: log and
  skip a permanently malformed message).
- A Plaid webhook receiver (sync is user-triggered only today).
- Off-box backup storage (`backup.sh` writes to the same volume it's
  backing up).
- A seeded demo dataset for a reviewer to explore without manually
  creating data first.
- Actually applying the Terraform once there's a real AWS account and
  budget to test against — see
  [ADR-0011](docs/adr/0011-terraform-written-not-applied.md) for exactly
  what remains unverified until then.

## Documentation links

- [`docs/case-study.md`](docs/case-study.md) — the portfolio narrative:
  problem, architecture, tradeoffs, real performance data, lessons
  learned, what's next
- [`docs/architecture.md`](docs/architecture.md) — system diagrams
- [`docs/api.md`](docs/api.md) — curated endpoint reference (the live
  Swagger UI at `/docs` is the authoritative schema)
- [`docs/security.md`](docs/security.md) — every security decision in
  one place
- [`docs/demo.md`](docs/demo.md) — a concrete walkthrough script
- [`docs/adr/`](docs/adr/) — architecture decision records:
  [0001](docs/adr/0001-async-sqlalchemy.md) async SQLAlchemy,
  [0002](docs/adr/0002-fail-open-redis-dependencies.md) fail-open Redis,
  [0003](docs/adr/0003-local-envelope-encryption-stand-in.md) envelope
  encryption stand-in, [0004](docs/adr/0004-event-contract-versioning.md)
  event versioning, [0005](docs/adr/0005-transactional-outbox.md)
  transactional outbox, [0006](docs/adr/0006-async-kafka-client.md)
  async Kafka client, [0007](docs/adr/0007-service-extraction-boundaries.md)
  service extraction boundaries,
  [0008](docs/adr/0008-grounded-insight-generation-with-fallback.md)
  grounded insight generation with fallback,
  [0009](docs/adr/0009-no-popup-close-animations.md) no popup close
  animations,
  [0010](docs/adr/0010-direct-otlp-export-no-collector.md) direct OTLP
  export, no Collector,
  [0011](docs/adr/0011-terraform-written-not-applied.md) Terraform
  written, not applied,
  [0012](docs/adr/0012-single-ec2-instance-sizing.md) single-EC2
  instance sizing,
  [0013](docs/adr/0013-per-ip-fixed-window-rate-limiting.md) per-IP
  fixed-window rate limiting,
  [0014](docs/adr/0014-market-data-service-no-kafka-topic.md)
  market-data-service without a Kafka topic
- [`docs/phase0.md`](docs/phase0.md), [`docs/phase1.md`](docs/phase1.md),
  [`docs/phase2.md`](docs/phase2.md), [`docs/phase3.md`](docs/phase3.md),
  [`docs/phase4.md`](docs/phase4.md), [`docs/phase5.md`](docs/phase5.md),
  [`docs/phase6.md`](docs/phase6.md), [`docs/phase7.md`](docs/phase7.md),
  [`docs/phase8.md`](docs/phase8.md), [`docs/phase9.md`](docs/phase9.md),
  [`docs/phase10.md`](docs/phase10.md), [`docs/phase11.md`](docs/phase11.md),
  [`docs/phase12.md`](docs/phase12.md), [`docs/phase13.md`](docs/phase13.md),
  [`docs/phase14.md`](docs/phase14.md), [`docs/phase15.md`](docs/phase15.md) —
  per-phase design notes and verification checklists

## Demo instructions

See [`docs/demo.md`](docs/demo.md) — a concrete ~10-minute walkthrough:
register, watch a transaction get categorized by the real pipeline,
trigger a live-pushed alert, generate an insight, find the resulting
trace in Tempo, and (optionally) run the chaos tests to see the
resilience claims proven live.
