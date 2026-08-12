# Architecture

System diagrams for Personal Finance Platform, current as of Phase 15 (all 16 phases
complete). For the reasoning *behind* each of these — not just what the
system looks like, but why it's shaped this way — see the per-phase
docs (`docs/phase0.md` onward) and the ADRs under `docs/adr/`. This
document is the map; those are the terrain notes.

## System overview

```mermaid
flowchart TB
    Browser["Browser<br/>(Next.js dashboard)"]

    subgraph Edge["Edge (production only)"]
        Nginx["nginx<br/>reverse proxy + TLS"]
    end

    subgraph Core["core-api (FastAPI)"]
        API["REST API<br/>+ WS /ws/live"]
        Outbox["Outbox publisher<br/>(background loop)"]
    end

    Postgres[("PostgreSQL<br/>source of truth")]
    Redis[("Redis<br/>cache, idempotency,<br/>rate limit, Pub/Sub")]
    Kafka["Redpanda<br/>(Kafka API)"]

    Enrichment["enrichment-service"]
    Anomaly["anomaly-service"]
    Notification["notification-service"]
    MarketDataService["market-data-service<br/>(scheduled poller,<br/>no Kafka topic)"]

    Plaid["Plaid API"]
    OpenAI["OpenAI API"]
    MarketData["Market data API"]

    Browser -->|"HTTPS"| Nginx
    Nginx -->|"/api/*, /ws/*"| API
    Nginx -->|"/"| Browser

    API <--> Postgres
    API <--> Redis
    API -->|"outbox row,<br/>same transaction"| Postgres
    Outbox -->|"poll every 3s"| Postgres
    Outbox -->|"publish (headers carry<br/>W3C traceparent)"| Kafka

    API -.->|"httpx"| Plaid
    API -.->|"httpx"| OpenAI
    API -.->|"httpx"| MarketData

    Kafka -->|"transactions.ingested"| Enrichment
    Enrichment -->|"transactions.enriched"| Kafka
    Enrichment <--> Postgres
    Enrichment -.->|"OpenAI fallback"| OpenAI

    Kafka -->|"transactions.enriched"| Anomaly
    Anomaly -->|"alerts.raised"| Kafka
    Anomaly <--> Postgres

    Kafka -->|"alerts.raised,<br/>insights.generated"| Notification
    Notification -->|"PUBLISH"| Redis
    Redis -->|"SUBSCRIBE"| API
    API -->|"WS push"| Browser

    MarketDataService <-->|"read tracked symbols,<br/>write latest_price_minor"| Postgres
    MarketDataService -.->|"httpx, batched"| MarketData
```

Every arrow into Postgres from `core-api` and every consumer service
uses its own minimal, explicitly-declared column subset rather than a
shared ORM model — a documented shared-database tradeoff, not an
accident (ADR-0007). The dotted lines to Plaid/OpenAI/market-data are
all optional integrations that degrade gracefully (a typed 503, or a
deterministic fallback) when unconfigured — see "External integration
behavior" in the root README. `market-data-service` has no edge to
Kafka at all — deliberately, since nothing consumes a `prices.updated`
event today (ADR-0014); it reads and writes Postgres directly on its
own schedule, independent of `core-api`'s on-demand refresh endpoint.

## Event pipeline: one request, one trace, four services

This is the sequence a single `POST /api/v1/transactions` call for an
*uncategorized* transaction actually walks through — and, since
Phase 12, the exact shape of the single distributed trace it produces
in Tempo (verified with real trace data, not just described here — see
`docs/phase12.md`).

```mermaid
sequenceDiagram
    participant U as Browser
    participant C as core-api
    participant P as Postgres
    participant OP as Outbox publisher
    participant K as Redpanda
    participant E as enrichment-service
    participant A as anomaly-service
    participant N as notification-service
    participant R as Redis

    U->>C: POST /api/v1/transactions
    activate C
    C->>P: INSERT transaction<br/>INSERT outbox_events (trace_headers captured here)
    Note over C,P: Same DB transaction — commit is atomic
    C-->>U: 201 Created
    deactivate C

    loop every 3s
        OP->>P: SELECT unpublished outbox rows
        OP->>K: produce (headers = captured traceparent)
        OP->>P: UPDATE published = true
    end

    K->>E: consume transactions.ingested
    activate E
    Note over E: continue_trace() extracts the<br/>traceparent — same trace as the<br/>original HTTP request
    E->>P: categorize (rules or OpenAI fallback)
    E->>K: produce transactions.enriched<br/>(new traceparent injected)
    deactivate E

    K->>A: consume transactions.enriched
    activate A
    Note over A: 3 rules evaluated:<br/>duplicate charge, spend spike,<br/>subscription price increase
    A->>P: INSERT alert (if a rule fires)
    A->>K: produce alerts.raised
    deactivate A

    K->>N: consume alerts.raised
    activate N
    N->>R: PUBLISH notifications:{user_id}
    deactivate N

    R-->>C: message on subscribed channel
    C-->>U: WS push over /ws/live
```

The idempotency guarantee that makes this safe under Kafka's
at-least-once delivery: `alerts` has a real `UNIQUE(source_event_id,
alert_type)` constraint, so a redelivered `transactions.enriched`
message re-evaluates the same rules and finds the alert already exists
rather than duplicating it (an earlier version of this was missing that
constraint despite the ADR claiming idempotency — see `docs/phase9.md`
for how that was caught).

## Auth: access tokens, rotating refresh, and WS tickets

```mermaid
sequenceDiagram
    participant U as Browser
    participant C as core-api

    U->>C: POST /auth/login
    C-->>U: access_token (body, 15 min)<br/>refresh_token (httpOnly cookie, 30 days)

    Note over U: access_token kept in memory only<br/>(Zustand, never localStorage — XSS-readable<br/>storage is the threat this avoids)

    U->>C: API calls with Authorization: Bearer
    C-->>U: 401 (token expired)
    U->>C: POST /auth/refresh (cookie rides along automatically)
    Note over C: Old refresh token checked:<br/>already used/revoked? → kill the<br/>whole token family (theft detection)
    C-->>U: new access_token + rotated refresh cookie
    U->>C: retry original request

    Note over U,C: WebSocket can't send a custom<br/>Authorization header
    U->>C: POST /auth/ws-ticket
    C-->>U: single-use ticket, 30s TTL
    U->>C: GET /ws/live?ticket=...
    Note over C: Long-lived token never touches<br/>a URL — narrows log/proxy exposure
```

## Observability data flow

```mermaid
flowchart LR
    subgraph Services["core-api + 3 workers"]
        direction TB
        S1[Traces: OTLP/http]
        S2["Metrics: /metrics<br/>(prometheus_client)"]
        S3["Logs: JSON to stdout<br/>(trace_id/span_id injected<br/>when a span is active)"]
    end

    Tempo["Tempo<br/>(trace storage)"]
    Prometheus["Prometheus<br/>(10s scrape)"]
    Promtail["Promtail<br/>(Docker log discovery,<br/>JSON parse)"]
    Loki["Loki<br/>(log storage,<br/>trace_id as structured metadata)"]
    Grafana["Grafana<br/>(dashboards +<br/>trace-to-logs link)"]

    S1 -->|"direct, no Collector<br/>(ADR-0010)"| Tempo
    S2 -->|"scraped"| Prometheus
    S3 -->|"docker logs"| Promtail
    Promtail --> Loki

    Tempo --> Grafana
    Prometheus --> Grafana
    Loki --> Grafana
    Grafana -.->|"tracesToLogsV2,<br/>filterByTraceID"| Grafana
```

The trace-to-logs link is genuinely functional — verified with real
data pulled from both Tempo's and Loki's own APIs during Phase 12, not
assumed from the config alone. Wiring the Grafana link alone isn't
enough on its own: without every log line carrying a `trace_id`, the
link has nothing to actually correlate against.

## Deployment topologies

Two Terraform environments exist; only one is meant to actually run —
see [ADR-0011](adr/0011-terraform-written-not-applied.md) for the full,
explicit accounting of what's applied versus what's validated-only.

```mermaid
flowchart TB
    subgraph SingleEC2["single-ec2 (the path meant to run)"]
        direction TB
        EC2["One t3.large EC2 instance<br/>(sized from real docker stats —<br/>ADR-0012)"]
        EC2Note["All 15 services via<br/>docker-compose.prod.yml.<br/>SSM Session Manager only —<br/>no SSH key, no open port 22."]
    end

    subgraph EKSPath["dev (EKS — written, never applied)"]
        direction TB
        EKS["EKS managed node group"]
        RDS[("RDS Postgres")]
        Elasticache[("ElastiCache Redis")]
        IRSA["IRSA: core-api pod assumes<br/>an IAM role via its<br/>Kubernetes service account"]
        KEDA["KEDA: Kafka-lag-based<br/>autoscaling for<br/>enrichment/anomaly"]
    end
```

See `deploy/README.md` for the actual single-EC2 deployment runbook
(first deploy, HTTPS, backups, rollback, total-loss recovery,
teardown) and `docs/phase14.md` for exactly which `terraform
validate`/`helm lint` commands were run against the EKS path and what
they did and didn't prove.
