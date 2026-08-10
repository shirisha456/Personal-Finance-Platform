# Phase 13 — Resilience and Chaos Testing

## Goal

Prove, against the real running stack — not by reading the code and
reasoning about it — that this project's two central resilience claims
actually hold: a consumer crashing doesn't lose work (the Kafka
consumer-group + outbox combination), and the API doesn't depend on
Kafka being reachable at all (the transactional outbox pattern itself,
ADR-0005). Two chaos scripts under `chaos/`, plus a CI job that runs
them against a real `docker compose up` stack on every push to `main`.

## What's here

- **`chaos/test_enrichment_recovery.py`** — kills `enrichment-service`
  mid-pipeline, creates a transaction, confirms it stays uncategorized
  while the consumer is down, restarts the container, confirms it gets
  categorized once the consumer group rejoins and reprocesses the
  pending message. References this project's actual compose container
  naming (`meridian-enrichment-service-1`).
- **`chaos/test_outbox_broker_outage.py`** — a different failure mode:
  the *broker itself* down, not a consumer. Stops Redpanda entirely,
  creates a transaction, and asserts three things: the request still
  succeeds (201) and does so in well under a second (not blocked on a
  Kafka connection attempt), `GET /ready` stays 200 throughout (the app
  has no synchronous Kafka dependency to report unready over), and the
  transaction correctly stays uncategorized until Redpanda comes back
  and the outbox publisher's retry loop catches up. This is the more
  fundamental of the two claims for this architecture specifically —
  the whole point of writing the outbox row in the same Postgres
  transaction as the business row (ADR-0005) is that a request never has
  to touch Kafka synchronously at all.

Both scripts use only the standard library (`urllib`, `subprocess`) —
no dependency install needed, so `python chaos/test_*.py` works with
nothing but the stack itself running.

## CI wiring

`.github/workflows/ci.yml`'s `chaos-smoke-test` job: gated to
`github.ref == 'refs/heads/main'` (needs `docker compose up --build` for
five services plus two container kill/restart cycles with real wait
times — too slow to run on every PR), depends on all four backend jobs
passing first, brings up Postgres/Redpanda/core-api/all three consumer
services, waits for `core-api`'s `/health`, runs both chaos scripts in
sequence, dumps `docker compose logs` on any failure, and tears the
stack down (`docker compose down -v`) unconditionally afterward.

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Two chaos scenarios, not one | Consumer-down *and* broker-down | Just the consumer-down scenario — leaves the outbox pattern's actual headline claim (the API never depends on Kafka) untested; a broker outage is a meaningfully different failure mode than one consumer dying, and this architecture makes a specific promise about it |
| Assertion style | Poll-with-timeout against real HTTP responses (`GET /api/v1/transactions/{id}`), not mocks or direct DB inspection | Checking the outbox table directly — would prove less: the actual user-facing contract is "does the transaction eventually get categorized," which only the API surface can confirm end-to-end |
| CI gating | `main`-only, not every PR | Every PR — each chaos run costs real wall-clock time (container kill + rejoin + rebalance + a 60s Redpanda health-repoll in the broker-outage test) for a class of bug (crash-recovery correctness) that changes rarely; `main`-only catches regressions before they reach anyone without slowing down routine PR iteration |

## Tradeoffs

- Neither script cleans up its test user/account/transactions afterward
  — each run leaves throwaway data behind (`chaos-<random>@example.com`).
  Acceptable for a CI job that tears down the entire stack (`docker
  compose down -v`, dropping the volume) immediately after; a real
  concern only if these were ever run against a persistent environment,
  which they're not designed for.
- No chaos coverage yet for `anomaly-service` or `notification-service`
  crashing (only `enrichment-service`, plus the shared broker-outage
  scenario which exercises the outbox generally). The recovery mechanism
  is identical for all three consumers (Kafka consumer-group offset
  tracking), so the marginal proof value of a third near-identical test
  is lower than it was for the first two — not added to keep this phase
  focused, not because the guarantee is unverified in principle.
- No chaos coverage for Postgres or Redis outages. Both are harder cases
  (Postgres going down mid-request has no graceful-degradation story —
  core-api's own health/readiness checks would correctly report
  unhealthy, which is the intended behavior, not a bug to prove around);
  worth a future phase's dedicated attention if this project's chaos
  coverage keeps growing.

## Verification checklist

- [x] `chaos/test_enrichment_recovery.py` run against the real stack —
      **PASSED**: transaction created while `enrichment-service` was
      killed stayed uncategorized (`category_id: null`), then was
      categorized (`category_id=7b24ca2d-...`, the seeded "Food &
      Dining" category) within seconds of the container restarting and
      the consumer group rejoining.
- [x] `chaos/test_outbox_broker_outage.py` run against the real stack —
      **PASSED**: transaction created in 0.03–0.06s with Redpanda
      stopped (not blocked), `GET /ready` returned 200 throughout the
      outage, the transaction correctly stayed uncategorized until
      Redpanda was restarted and confirmed healthy via `rpk cluster
      health`, then was categorized once the outbox publisher's next
      retry cycle picked it up.
- [x] Both scripts verified independently, each run to completion
      against a real `docker compose up` stack (Postgres, Redis,
      Redpanda, core-api, enrichment-service, anomaly-service,
      notification-service) — not simulated, not mocked.
- [x] `.github/workflows/ci.yml` — YAML validated
      (`python -c "import yaml; yaml.safe_load(...)"`); the
      `chaos-smoke-test` job's structure (start stack → wait for health
      → run both scripts → dump logs on failure → always tear down) was
      not run through actual GitHub Actions in this environment (no CI
      runner available here), so it's confirmed syntactically correct
      and logically consistent with the manual runs above, not
      confirmed to pass in GitHub's own runner environment specifically.
