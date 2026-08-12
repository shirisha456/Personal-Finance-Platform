# Demo walkthrough

A concrete, ~10-minute script for seeing the whole system work — the
event pipeline, live push, and observability all in one pass. Every
step here is something that was actually run against the real stack
during development (see the phase docs' verification checklists for
the original runs) — this is that same walkthrough, written for someone
else to follow.

## 1. Start the stack

```bash
docker compose up -d --build
```

Wait for everything to report healthy:
```bash
docker compose ps
```

In a second terminal, start the frontend:
```bash
cd web
npm install    # first time only
npm run dev
```

Open `http://localhost:3000` — it should redirect to `/login`.

## 2. Register and look around

Register a new account through the UI. You'll land on the dashboard —
empty at first (no insight yet, no accounts). Add an account (Accounts →
Add account) with a currency of your choice.

## 3. Watch the pipeline: create a transaction, see it get categorized

Add a transaction (Overview → Add transaction) **without** picking a
category — leave it uncategorized. Within a few seconds
(`outbox_publisher`'s 3-second poll interval + real Kafka
produce/consume latency), reload the transactions list: it now has a
category, assigned by `enrichment-service`'s rules engine (or its
OpenAI fallback, if `OPENAI_API_KEY` is set — see "External integration
behavior" in the root README).

**What actually happened**: the `POST /transactions` call wrote both
the transaction row and an outbox row in one Postgres transaction; the
outbox publisher's background loop picked it up and published
`transactions.ingested` to Redpanda; `enrichment-service` consumed it,
categorized it, and published `transactions.enriched`.

## 4. Trigger a live alert

Create the *same* transaction again (same merchant, same amount, same
account) within a day of the first one. This trips `anomaly-service`'s
duplicate-charge rule. Within a few seconds, the bell icon in the
dashboard header should show an unread badge — **pushed live over the
WebSocket**, no page refresh. Click it to see the alert.

**What actually happened**: `anomaly-service` consumed the second
`transactions.enriched` event, evaluated its three rules, found a
duplicate, wrote the alert (idempotently — a redelivery of the same
event would not create a second alert), and published `alerts.raised`.
`notification-service` consumed that and published to
`notifications:{user_id}` on Redis; `core-api`'s `/ws/live` connection
(authenticated via a single-use ticket minted just for that connection)
was subscribed and relayed it straight to the browser.

## 5. Generate an insight

Dashboard → Monthly insight → Generate. With at least one categorized
expense in the current month, this produces a real summary — either a
deterministic template, or an OpenAI-generated one if configured (see
`docs/phase10.md` and
[ADR-0008](adr/0008-grounded-insight-generation-with-fallback.md) for
why the model only ever sees pre-computed aggregates, never raw
transactions).

## 6. See the whole thing as one trace

```
docker compose up -d tempo prometheus loki promtail grafana
```

Open Grafana at `http://localhost:3001` (anonymous admin access in
dev — no login). The "Personal Finance Platform — Pipeline Overview" dashboard is
already provisioned. To see the actual distributed trace from step 3:

1. Go to Explore → select the **Tempo** datasource.
2. Search for a recent trace on `core-api`, service `POST
   /api/v1/transactions`.
3. Open it — you should see the HTTP request span, its SQLAlchemy
   INSERT spans, and nested underneath: `enrichment-service.process`,
   `anomaly-service.process`, `notification-service.process` — one
   connected trace across all four services, not four separate ones
   (verified for real in `docs/phase12.md`).
4. Click through to logs from a span — Tempo's trace-to-logs link
   pulls the exact log lines tagged with that trace's ID from Loki.

## 7. Bonus: prove the resilience claims

With the full stack running:

```bash
python chaos/test_enrichment_recovery.py
python chaos/test_outbox_broker_outage.py
```

The first kills `enrichment-service` mid-pipeline and shows a
transaction created while it's down stays uncategorized until it comes
back — no data lost. The second stops the Kafka broker entirely and
shows a transaction still gets created in under a second regardless —
the API never depends on Kafka being reachable. See `docs/phase13.md`
for what these actually proved when first run.

## Login for a pre-seeded demo

There isn't one — every environment starts empty; step 2 above is the
fastest path to real data. Seeding a demo dataset (a handful of
realistic accounts/transactions/goals for a reviewer to explore without
manually creating them) is a reasonable future addition, not built here
— see the root README's "Future enhancements".
