# Phase 1 — Core API and Persistence

## Goal

Stand up a FastAPI application with real, working plumbing — configuration,
async database access, structured logging, a centralized error-response
model, and liveness/readiness health checks — that every later phase's
domain code builds on, without any domain logic yet.

## Architecture

```
Client
  → FastAPI app (app/main.py::create_app)
  → CORS middleware
  → exception handlers (AppError / validation / HTTP / catch-all)
  → health router (/live, /ready, /health)
  → AsyncSession (app/core/db.py) → PostgreSQL
```

Cross-cutting, available to every module added from Phase 2 onward:

- `app/core/config.py` — `Settings` (pydantic-settings), cached via
  `get_settings()`
- `app/core/db.py` — async engine/session, `Base` declarative metadata,
  `get_db` dependency
- `app/core/logging.py` — JSON logs outside development, human-readable
  logs in it
- `app/errors/` — an `AppError` hierarchy + handlers that turn any error
  (deliberate, validation, routing, or unhandled) into one consistent
  `{"error": {"type", "message", "details"}}` envelope, documented in
  `docs/api.md` once it exists (Phase 15)

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Database access | Async SQLAlchemy 2.0 + asyncpg (see [ADR-0001](adr/0001-async-sqlalchemy.md)) | Sync SQLAlchemy in async routes |
| Error handling | A small `AppError` exception hierarchy + four registered FastAPI exception handlers, one consistent JSON envelope for every error path (deliberate, validation, 404/405, and unhandled) | Ad hoc `HTTPException(status, detail)` per route — consistent by convention only, and an unhandled exception fell through to FastAPI's default, uncontrolled response |
| Health endpoints | Separate `/live`, `/ready`, `/health` — `/ready` returns a real 503 when the database is unreachable (what a Kubernetes readinessProbe checks), `/health` always returns 200 with status in the body (what a dashboard scrapes) | A single `/health` returning `{"status": "ok"}` unconditionally — no way to distinguish "process up" from "process up but can't serve real requests" |
| Logging | Structured JSON in non-development environments, human-readable in development, one shared formatter | No centralized logging config — every module called `logging.getLogger(__name__)` with no shared shape, nothing shippable to Loki without ad hoc parsing |
| Packaging | `pyproject.toml` (PEP 621), installed as an editable package | `requirements.txt` |

## Tradeoffs

- Async SQLAlchemy raises the bar for correctness (see ADR-0001's
  consequences) — this is accepted deliberately, not overlooked.
- The `AppError` hierarchy is intentionally small (five subclasses) rather
  than one class per possible failure — new error types are added when a
  route actually needs one, not speculatively.

## Extensibility

Adding a new domain module in a later phase means:

1. Add `app/<module>/models.py` defining tables against `app/core/db.py`'s
   `Base`, and import that module in `alembic/env.py` so autogenerate sees it.
2. Add `app/<module>/router.py`, raising `AppError` subclasses (or adding
   a new one) instead of hand-building `HTTPException` — the envelope is
   then automatic.
3. Register the router in `app/main.py::create_app`.

No changes to `core/`, `errors/`, or `health/` are needed for a
well-behaved module.

## Verification checklist

- [x] `docker compose config` — `core-api` service parses correctly
- [x] `alembic.ini` / `alembic/env.py` load without error (zero migrations
      yet — Phase 2 adds the first one)
- [x] `pytest -v` — 12 tests, all passing (health liveness/readiness/health
      aggregation for both reachable and unreachable database states, the
      error envelope for deliberate/unhandled/unknown-route errors, config
      parsing)
- [x] `ruff check .` — clean
- [x] CI `backend` job runs migrations + tests against a real Postgres
      service container, not just SQLite
