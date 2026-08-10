# ADR-0004: Event schema versioning via a payload field, not the topic name

## Status

Accepted

## Context

Event contracts change over time — a field gets added, a type
narrows, a new required field appears. A consumer running old code and
a producer running new code will coexist during any rolling deploy.
Something has to let a consumer know which shape of a message it's
looking at. Without a versioning mechanism — no `version` field on any
event class, no version in any topic name — a consumer has no way to
detect a schema change short of a runtime validation failure.

## Decision

Every event contract inherits `BaseEvent`, which carries `version: int
= 1` directly in the payload, alongside a real `event_id` (UUID) for
consumer-side deduplication. Topic names stay stable
(`transactions.ingested`, not `transactions.ingested.v1`).

## Alternatives considered

- **Version in the topic name** (`transactions.ingested.v2`) — a common
  pattern, but it means a breaking schema change requires provisioning a
  new topic, migrating every consumer's subscription, and running both
  topics in parallel during the transition. For a payload-shape change
  that doesn't affect partitioning or ordering guarantees, that's a lot
  of infrastructure churn for what a version field in the message body
  handles more cheaply.
- **No versioning at all** — rejected outright; a consumer has no way
  to tell "this field is missing because
  the event predates it" from "this field is missing because something
  is broken."

## Consequences

- A consumer that cares about compatibility should branch on
  `event.version` (or reject versions it doesn't understand) rather than
  assuming every message on a topic has the current shape — this
  discipline isn't enforced by the type system, only documented here and
  in `docs/event-pipeline.md` (Phase 15).
- Additive, optional-field changes don't need a version bump in practice
  (Pydantic simply ignores unknown fields by default and treats missing
  optional fields as their default) — bumping `version` is for changes
  an old consumer would actually misinterpret, not every schema edit.
- `event_id` existing now is what makes anomaly-service's idempotency
  guarantee (Phase 9) possible at all — deduplicating a redelivered
  message needs a stable per-event identifier to key off of.

## Validation

`libs/events/tests/test_schemas.py` asserts every event class defaults
`version=1` and generates a unique `event_id` per instance. Consumer-side
idempotency using `event_id` is validated in Phase 8/9's consumer tests,
once those consumers exist.
