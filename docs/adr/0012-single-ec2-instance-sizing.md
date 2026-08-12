# ADR-0012: Single-EC2 instance sizing (`t3.large`)

## Status

Accepted

## Context

`infra/terraform/envs/single-ec2` provisions the one instance meant to
actually run this platform in production (see
[ADR-0011](0011-terraform-written-not-applied.md)). It needs a real
instance size, not a guess — this ADR records the measurement this was
based on, so the reasoning can be checked and redone if the stack's
footprint changes.

## Measurement

`docker stats --no-stream` against this project's own local dev stack —
all 12 backend + observability containers running simultaneously,
idle (no active request load) — measured directly, not estimated:

| Container | Idle memory |
|---|---|
| redpanda | 131.2 MiB |
| core-api | 107.2 MiB |
| enrichment-service | 87.3 MiB |
| tempo | 84.4 MiB |
| loki | 60.5 MiB |
| anomaly-service | 58.5 MiB |
| grafana | 64.1 MiB |
| notification-service | 43.7 MiB |
| postgres | 45.7 MiB |
| promtail | 39.2 MiB |
| prometheus | 27.9 MiB |
| redis | 4.0 MiB |
| **Total (measured)** | **≈ 754 MiB** |

`web` (the Next.js frontend) and `nginx` aren't part of the local dev
compose stack (the frontend runs via `npm run dev` outside Docker in
dev — see `docs/phase11.md`), so they're **not measured, only
estimated** below: nginx is well-documented to idle under 20 MiB;
Next.js's standalone production server is reasonably estimated at
100–150 MiB idle. Flagged explicitly as estimated, not measured, per
this project's own honesty rule against presenting a guess as data.

## Decision

Idle usage (≈754 MiB measured + ~150–200 MiB estimated for web+nginx,
call it ~950 MiB–1 GiB total) is the *floor*, not the number to size
against — the same reasoning the methodology here follows regardless of
the exact idle figure: real traffic spikes, and metrics/logs/traces
accumulate in memory between scrape/flush intervals in a way idle
measurement doesn't capture. So instance sizing here is done against
**production memory *limits*** (`deploy/docker-compose.prod.yml`'s
`deploy.resources.limits.memory` on every service), each chosen with
headroom over its own measured idle number, not the raw idle sum:

| Service | Idle (measured) | Limit (chosen) |
|---|---|---|
| postgres | 45.7 MiB | 512 MiB |
| redis | 4.0 MiB | 256 MiB |
| redpanda | 131.2 MiB | 768 MiB |
| core-api | 107.2 MiB | 512 MiB |
| web | *(est. 100–150)* | 384 MiB |
| enrichment-service | 87.3 MiB | 384 MiB |
| anomaly-service | 58.5 MiB | 256 MiB |
| notification-service | 43.7 MiB | 192 MiB |
| tempo | 84.4 MiB | 384 MiB |
| prometheus | 27.9 MiB | 512 MiB |
| loki | 60.5 MiB | 384 MiB |
| promtail | 39.2 MiB | 128 MiB |
| grafana | 64.1 MiB | 256 MiB |
| nginx | *(est. <20)* | 64 MiB |
| **Total limits** | | **4,992 MiB ≈ 4.87 GiB** |

Plus OS + Docker daemon overhead (typically 300–500 MiB on a minimal
Ubuntu server), committed usage under full load is realistically
**≈5.2–5.4 GiB**.

**`t3.medium` (4 GiB) does not fit** — the limits sum alone (4.87 GiB)
already exceeds it, before any OS overhead. **`t3.large` (8 GiB) leaves
≈2.6–2.8 GiB headroom (≈32–35%)**, which is the instance type chosen —
`infra/terraform/envs/single-ec2/variables.tf`'s `instance_type`
default, with a comment pointing back at this ADR specifically warning
against dropping to `t3.medium`.

## Alternatives considered

- **Size against idle usage directly** — rejected. ~1 GiB idle would
  make `t3.small` (2 GiB) look sufficient, but that ignores that
  Prometheus's memory grows with time-series cardinality and retention,
  Loki/Tempo grow with log/trace volume, and any real request traffic
  on core-api/the workers adds real usage on top of idle. Sizing against
  limits (a number chosen deliberately, not a fixed data-dependent
  measurement) is the only approach that accounts for growth.
- **`t3.xlarge` (16 GiB) for extra safety margin** — rejected as
  unnecessary cost for a personal portfolio project; `t3.large`'s ~33%
  headroom over the actual limits sum is comfortable without paying for
  capacity this stack has no realistic path to using.
- **A 2GB swapfile as the *primary* mitigation instead of right-sizing
  the instance** — rejected as the primary strategy (swap under memory
  pressure on a network-attached-storage-backed EBS volume is slow
  enough to functionally look like an outage), but kept as cheap,
  free second-line defense — see
  `infra/terraform/envs/single-ec2/user_data.sh.tftpl`, which provisions
  one automatically using already-paid-for EBS space.

## Consequences

- If a future phase adds another service to this stack, the correct
  process is the same one used here: measure its actual idle footprint
  with `docker stats`, don't guess — then re-evaluate whether `t3.large`
  still has enough headroom or a bigger instance (or offloading the
  observability stack to a managed service) is warranted.
- The `web`/`nginx` estimates in this ADR should be replaced with real
  measurements the first time the full `deploy/docker-compose.prod.yml`
  stack is actually run somewhere (even locally) — flagged here as a
  known gap in this measurement, not silently treated as equivalent to
  the measured numbers above.

## Validation

The table under "Measurement" is the literal output of
`docker stats --no-stream` run against this project's real local
dev stack (`docker compose ps` showing all 12 containers `Up`) during
Phase 14 — not simulated, not copied from another project. The
`t3.medium`-doesn't-fit / `t3.large`-fits arithmetic in "Decision" is
directly computable from the limits table above.
