# ADR-0001: Async SQLAlchemy 2.0 for database access

## Status

Accepted

## Context

The core API is a FastAPI application, whose entire value proposition over
a sync framework is that a single worker process can hold many requests
in flight while each awaits I/O (database queries, external API calls).
A common but flawed approach is to use sync SQLAlchemy `Session` objects
inside `async def` route handlers — every database call then runs on
FastAPI's default threadpool, which works, but silently caps concurrency
at the threadpool size and means "async correctness" isn't actually true
end-to-end, only at the HTTP-framing layer.

## Decision

Use SQLAlchemy 2.0's native async API throughout: `create_async_engine`,
`AsyncSession`, `async_sessionmaker`, with `asyncpg` as the runtime driver.
Every route, service function, and repository call that touches the
database is `async def` down to the query itself — no sync session
anywhere in the request path.

Alembic migrations are the one deliberate exception: they run against a
synchronous driver (`psycopg`) via a URL swap in `alembic/env.py`, since a
one-shot schema migration has no concurrency to preserve and async
Alembic adds real complexity (a separate async migration runner) for no
observable benefit in a single-writer, run-once-at-deploy-time script.

## Alternatives considered

- **Sync SQLAlchemy in async routes** — simpler, more mature
  tooling/error messages, and
  perfectly adequate at prototype request volumes. Rejected because it
  doesn't hold up as the concurrency story a portfolio project should be
  able to defend under questioning, and it silently degrades (queueing on
  the threadpool) rather than failing loudly once real concurrent load
  arrives.
- **Async Alembic** (running migrations through the same async engine as
  the app) — rejected for the reason above: no concurrency benefit for a
  script that runs once per deploy, at the cost of a less-standard
  migration setup.

## Consequences

- Every test that exercises a route touching the database needs an async
  test client (`httpx.AsyncClient` over `ASGITransport`) and either a real
  Postgres or an async-capable SQLite driver (`aiosqlite`) — both are
  wired up in `tests/conftest.py`.
- The dependency footprint grows by one driver: `asyncpg` for the app,
  `psycopg[binary]` for Alembic only.
- Any future ORM code must not accidentally call a sync-only API (e.g. a
  lazy-loaded relationship accessed outside an active session) — this is
  a real category of bug async SQLAlchemy makes easier to introduce than
  sync SQLAlchemy does, and something to watch for in review from Phase 2
  onward once relationships exist.

## Validation

`apps/core-api/tests/test_health.py` exercises the async session
dependency end-to-end (both the "database reachable" and "database
unreachable" paths) through the real `AsyncSession`/`get_db` machinery,
not a mock. CI (`backend` job in `.github/workflows/ci.yml`) runs
`alembic upgrade head` against a real Postgres service container, proving
the sync-driver migration path works against the same database the async
app runtime connects to.
