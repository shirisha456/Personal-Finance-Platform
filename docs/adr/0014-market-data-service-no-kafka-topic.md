# ADR-0014: market-data-service is a scheduled poller with no Kafka topic

## Status

Accepted

## Context

`docs/phase5.md` and `docs/phase7.md` both flagged extracting a
standalone `market-data-service` as real, planned future work once the
event pipeline existed — and both specifically imagined it as a
Kafka *producer*, publishing a `prices.updated` event. That event
pipeline (Phase 7 onward) now exists. This ADR is the "whichever phase
does it" those docs pointed to, and answers the question they left open:
now that Kafka exists, should this service actually publish to it?

## Decision

No. `services/market-data-service` is a plain scheduled poller —
`asyncio.sleep`-loop, not an `AIOKafkaProducer` — that reads every
tracked security's symbol from Postgres, prices it via the same
Twelve Data provider `core-api`'s on-demand refresh endpoint already
uses, and writes `latest_price_minor`/`latest_price_at` directly back to
the `securities` table it doesn't own (minimal-column-subset contract,
same shape as `enrichment-service`/`anomaly-service`'s — ADR-0007).

No `prices.updated` topic is defined, because nothing in this codebase
would consume it today. That is exactly the "dead contract" problem
`docs/phase7.md` explicitly refused to create when Phase 7 itself was
scoped ("market data is an on-demand endpoint, not a Kafka producer...
defining those two topics now would create a dead contract — an event
schema with no consumer, silently untested and unused"). Building the
topic anyway, now, for the same reason that was rejected then, would be
inconsistent with this project's own stated engineering standard — not
a stricter reading of the original phase docs, but the same one applied
at the point it was actually supposed to be revisited.

## Alternatives considered

- **Publish `prices.updated`, let a future phase consume it** — rejected
  for the dead-contract reason above. If a real consumer shows up later
  (e.g. `notification-service` alerting on a large single-day portfolio
  move), that's the point to add the topic *and* its first consumer
  together, not before either exists.
- **Keep market data purely on-demand, no standalone service at all** —
  rejected: this is the specific gap the original 12-milestone plan
  (M8) called for, and every user having to manually click refresh for
  their portfolio to ever show a price is worse than a scheduled poller
  keeping prices fresh in the background, independent of the on-demand
  endpoint (which stays — it's still useful for "I want this number
  right now," not just left for the poller alone).
- **Have the poller write through core-api's HTTP API instead of the
  database directly** — rejected: would mean depending on core-api's
  process being up for what is otherwise a fully self-contained batch
  job, and adds authentication complexity (a service-to-service token)
  for no benefit over the same direct-DB-write pattern every other
  extracted service already uses.

## Consequences

- A price update from this service is invisible to anything that isn't
  either reading `securities` directly or hitting `GET /investments`
  afterward — there is no live push (no WebSocket alert, no Kafka event)
  when a price changes, unlike alerts/insights. Acceptable: price
  freshness is a background-refresh concern, not a real-time one, at
  this project's scale.
- Two independent price-update paths now exist (the on-demand endpoint
  and this poller) writing the same columns. Both are idempotent
  UPDATE-by-symbol, so there's no correctness conflict — the poller
  might overwrite a price the on-demand endpoint just fetched seconds
  earlier with an equally-current one, which is a no-op in practice, not
  a race worth guarding against here.

## Validation

`services/market-data-service/tests/test_poller.py` and `test_db.py`:
a not-configured provider and an empty tracked-security set both no-op
cleanly, prices are written only for symbols the provider actually
returned, an untracked/unpriced symbol is left alone rather than zeroed,
and a batch that raises doesn't cost an already-successful batch its
update (proven by forcing a small batch size and one failing symbol).
