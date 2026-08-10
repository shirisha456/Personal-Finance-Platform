# ADR-0010: Every service exports OTLP traces directly to Tempo — no Collector

## Status

Accepted

## Context

Phase 12 adds distributed tracing across `core-api` and the three Kafka
consumer services, plus Prometheus metrics and structured logging shipped
to Loki. A common production pattern is to run an OpenTelemetry Collector
as a fan-in point: every service exports to the Collector, which then
batches, samples, and forwards to the actual backend(s) (Tempo, and
potentially others later). This ADR is about whether to introduce one now.

## Decision

No Collector. Every service (`core-api`, `enrichment-service`,
`anomaly-service`, `notification-service`) configures its own
`OTLPSpanExporter` pointed directly at Tempo's OTLP/http receiver
(`http://tempo:4318`), via the same opt-in `OTEL_EXPORTER_OTLP_ENDPOINT`
setting each service already has (empty by default — see each service's
`app/{core/,}tracing.py`).

## Alternatives considered

- **An OTel Collector in front of Tempo** — rejected for now. Its value
  (multi-backend fan-out, tail sampling, PII scrubbing, protocol
  translation) doesn't apply to a single-node local stack with one trace
  backend and no compliance requirement to scrub spans before they land.
  Adding it would be a fifth moving part with no capability this project
  currently uses, for a four-service backend already asking a lot of a
  single Docker Compose file.
- **No tracing at all, metrics/logs only** — rejected. The whole point of
  Phase 12 given this project's architecture (an event-driven pipeline
  spanning four independently-deployable services connected by Kafka) is
  to make the actual causal chain — one HTTP request → an outbox row →
  a Kafka message → enrichment → another Kafka message → anomaly
  detection → a third Kafka message → notification — visible as *one*
  trace instead of four services' logs a human has to correlate by hand
  and by timestamp. That chain, and specifically getting Kafka's lack of
  built-in trace propagation right (traceparent hand-carried in message
  headers, captured at outbox-write time — see
  `apps/core-api/app/core/tracing.py::capture_trace_headers` and each
  worker's `continue_trace`/`inject_trace_headers`), is the single
  highest-value thing this phase does. Real, verified result: a
  `POST /api/v1/transactions` trace correctly contains
  `enrichment-service.process` → `anomaly-service.process` →
  `notification-service.process` as nested child spans, not four
  disconnected traces (see `docs/phase12.md`'s verification checklist).

## Consequences

- If this project ever needs tail sampling, multi-backend export, or
  Collector-side processing, that's a Collector added later in front of
  the same OTLP endpoint every service already targets — moving the URL
  each service points at, not re-instrumenting anything.
- Every service independently owns its own `BatchSpanProcessor` queue and
  export retry behavior; there's no shared batching/backpressure point.
  Fine at this scale (a handful of services, local dev traffic volumes),
  worth revisiting if traffic or service count grows enough that
  per-service export overhead becomes measurable.
- Tempo's OTLP/http port (4318) is published to the host specifically so
  a service run outside Docker (e.g. `uvicorn --reload` directly) can
  still export traces during development.

## Validation

Full pipeline traced end-to-end against the real running stack: a real
`POST /api/v1/transactions` request produced a single Tempo trace
containing `meridian-core-api`'s HTTP/SQLAlchemy spans, then
`enrichment-service.process` (with its own SQLAlchemy child spans),
then `anomaly-service.process` (same), then `notification-service.process`
— fetched and inspected directly via Tempo's `/api/traces/{traceID}`
API, confirming correct `parentSpanId` chaining across all four
services from a single HTTP request. See `docs/phase12.md`.
