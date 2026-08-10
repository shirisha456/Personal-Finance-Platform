# Case Study: Meridian

## Problem statement

Personal finance data is scattered across banks, brokerages, and manual
tracking. Turning it into one accurate picture — spending by category,
budget adherence, net worth, investment performance, anomalies worth
noticing — usually means a spreadsheet, or trusting a third party with
read access to every account.

This project's goal was narrower and more specific than "build a finance
app": build it phase by phase with clean, reviewed history, and along
the way, actually verify every claim rather than trusting it on paper —
an event-driven pipeline that's supposedly idempotent, healthchecks that
supposedly exist, migrations that are supposedly reversible. Several of
those assumptions turned out to be false once actually tested. Finding
and fixing them, not just designing the architecture, was the real work.

## Architecture

An async FastAPI core (`core-api`) owns the primary database and every
synchronous read/write. Three independently-deployable Kafka consumers
(`enrichment-service`, `anomaly-service`, `notification-service`) do
asynchronous work off an event pipeline: categorize a transaction,
detect anomalies, push live notifications. A transactional outbox
(write the event in the same DB transaction as the business row, a
separate publisher process ships it to Kafka after commit) means the
API request path never has a synchronous Kafka dependency. A Next.js
dashboard talks to the API and a ticket-authenticated WebSocket for live
updates. Full tracing/metrics/logging (OpenTelemetry → Tempo,
Prometheus, Loki) makes the whole pipeline — one HTTP request fanning
out across four services — visible as one connected trace, not four
services' logs correlated by hand.

## Scalability decisions

| Decision | Reasoning |
|---|---|
| Async SQLAlchemy end-to-end, not sync-in-a-threadpool | No thread-pool exhaustion under concurrent request load; one execution model, not two |
| Kafka-lag-based autoscaling (KEDA) for consumers, not CPU-based | An I/O-bound consumer waiting on Postgres/OpenAI can have near-zero CPU with a large backlog — CPU utilization doesn't reflect the actual bottleneck |
| Redis-backed idempotency keys + response caching | Cheap protection against duplicate submits and repeat work, explicitly fail-open so a cache outage degrades gracefully instead of blocking writes |
| Pagination on every high-cardinality resource | Accounts/transactions/goals/holdings return `{items, total, limit, offset}` — bounded response size regardless of how much data a user accumulates |
| Managed node group + IRSA (EKS path) | Federated, short-lived pod credentials — no long-lived AWS keys baked into an image, scoped exactly to what each pod needs |

## Reliability features

- **Transactional outbox**: a transaction being created never depends
  on Kafka being reachable — proven, not just claimed, by a chaos test
  that stops the broker entirely and confirms the API still responds in
  under a second.
- **Real idempotency**: a `UNIQUE(source_event_id, alert_type)`
  constraint backs anomaly-service's alert creation. An earlier version
  of this was missing the constraint despite the architecture doc
  claiming idempotency — caught by a test that simulates Kafka's
  at-least-once redelivery and asserts no duplicate alert gets created,
  not by review.
- **Rotating refresh tokens with theft detection**: a refresh token
  presented twice (already used or revoked) kills its entire token
  family, not just itself.
- **Migration reversibility, proven every phase**: every migration has
  been round-tripped (`upgrade → downgrade base → upgrade`) against real
  Postgres, not written and assumed correct — this caught a real bug
  (Postgres native ENUM types outliving `DROP TABLE`) in two separate
  migrations before it could bite in production.
- **Chaos-tested, not just designed**: two chaos scripts against the
  real running stack — a consumer crashing mid-pipeline, and the Kafka
  broker itself going down — both pass, both actually run (not
  simulated), both gated in CI on every push to `main`.

## Tradeoffs

| Choice | What it costs |
|---|---|
| Everything async | Alembic migrations needed a sync driver swapped in specifically for that one-shot use case — async gained nothing there and added complexity if forced |
| Transactional outbox | Eventual consistency, not immediate — a transaction can be momentarily uncategorized while the publish/consume cycle catches up; an extra table and a background process to operate |
| Shared Postgres instance across all four services | Services aren't independently deployable at the data layer yet — each connects directly via a minimal, explicitly-declared column subset rather than a real per-service database; a future split is a connection-string change, not a code change, but it hasn't happened |
| Fail-open Redis for idempotency/caching | Under a Redis outage, duplicate-submit protection silently stops working — an accepted, documented tradeoff for a personal-finance app with no real money movement in these specific calls, not the right call for every use case |
| No OpenTelemetry Collector | No sampling, no multi-backend fan-out, no processing layer — fine for one Tempo backend and local-dev traffic volumes, a real constraint if either changes |
| Terraform written, never applied | Every real-world surprise `terraform apply` would surface (account quotas, IAM sufficiency, actual security group behavior) remains genuinely unverified — stated explicitly as a boundary, not glossed over |

## Performance results

Honestly scoped: **no formal load or stress testing was performed** —
there's no k6/Locust harness in this project, and no p95-latency-under-
concurrent-load number would be real if reported here. What *is* real,
measured data:

- **168+ automated tests** passing across 6 backend packages (132
  core-api, 24 enrichment-service, 17 anomaly-service, 5
  notification-service, 11 market-data-service, 6 shared event
  contracts).
- **≈754 MiB idle memory** for the full 12-container backend +
  observability stack, measured directly with `docker stats` — the
  actual basis for the production instance-sizing decision (t3.large,
  not a guess), not an estimate.
- **Sub-second write path under total broker failure**: a transaction
  create request completed in 0.03–0.06 seconds with the Kafka broker
  entirely stopped, confirming the transactional outbox pattern's actual
  claim (no synchronous Kafka dependency on the request path) rather
  than just its design intent.
- **Full pipeline recovery, observed end-to-end**: a transaction
  created while `enrichment-service` was killed stayed uncategorized
  until the container restarted, then was correctly categorized once
  the consumer group rejoined — no manual intervention, no data loss.

## Lessons learned

1. **Running real infrastructure finds bugs static review can't.**
   Every one of this project's most significant fixes — the Postgres
   ENUM cleanup bug, a session that silently dropped on every frontend
   reload, closed dialogs that never actually unmounted, the
   anomaly-service idempotency gap — was found by actually running the
   thing, not by reading the code carefully.
2. **Your own documentation isn't proof.** An ADR claiming idempotency
   doesn't make the code idempotent, and a design doc claiming a
   healthcheck exists doesn't mean it ships. "The ADR says X" and "X is
   actually true" are different claims; only testing closes that gap.
3. **Measure, don't estimate, when a real number is available.**
   Instance sizing from actual `docker stats` output beat guessing —
   and immediately made an otherwise-invisible fact obvious (`t3.medium`
   doesn't even fit the production resource limits, before OS overhead).
4. **Distributed tracing across Kafka needs deliberate propagation
   design.** HTTP middleware carries trace context for free; a message
   queue doesn't. Getting one connected trace across four services
   required hand-carrying a W3C traceparent through outbox rows and
   Kafka message headers at every hop — it's not automatic just because
   OpenTelemetry is installed.
5. **Saying "not verified" out loud is itself a discipline worth
   keeping.** It would have been easy to write Terraform and Helm charts
   and imply they work. Writing down exactly what static validation
   does and doesn't prove — in a permanent, explicit ADR — is more
   useful to a reader than silence on the question.

## What's next

- A dead-letter topic for the three Kafka consumers (currently: log and
  skip a permanently malformed message).
- A Plaid webhook receiver (sync is user-triggered only today).
- Off-box backup storage (`backup.sh` currently writes to the same
  volume it's backing up).
- A seeded demo dataset for a reviewer to explore without manually
  creating data first.
- Actually applying the Terraform, once there's a real AWS account and
  budget to do it against — and closing every gap
  [ADR-0011](adr/0011-terraform-written-not-applied.md) lists as
  unverified.
