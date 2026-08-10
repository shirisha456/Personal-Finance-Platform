# Phase 14 — Infrastructure and CI/CD

## Goal

Real, reviewable infrastructure-as-code for actually running Meridian
somewhere: Terraform (two environments — a full EKS-based `dev`
environment, and a `single-ec2` environment that's the one path meant
to actually be applied), Helm charts for the EKS path, a production
`docker-compose.prod.yml` + nginx + backup/restore scripts for the
single-EC2 path, and a new CI job that statically validates all of it.
Scoped to this project's 3-worker topology as it stood at this phase
(`market-data-service` was added later, as a standalone poller — see
the README's "Post-phase-15 additions" and
[ADR-0014](adr/0014-market-data-service-no-kafka-topic.md)).

The single most important thing to get right in this phase wasn't a
design decision — it was being explicit about what "written" means
versus what "verified" means. See
[ADR-0011](adr/0011-terraform-written-not-applied.md).

## What's here

```
infra/terraform/modules/{vpc,eks,rds,elasticache,iam}/   reusable building blocks
infra/terraform/envs/dev/                                  wires all 5 modules — EKS, never applied
infra/terraform/envs/single-ec2/                            one EC2 instance — the path meant to run
infra/helm/core-api/                                        Deployment/Service/HPA/Ingress/ServiceAccount
infra/helm/worker/                                           one chart, one values file per service
  values-enrichment.yaml / values-anomaly.yaml / values-notification.yaml
deploy/
  docker-compose.prod.yml    real service, resource limits from real measurements
  nginx/{nginx.conf,nginx-ssl.conf.template}
  scripts/{backup.sh,restore.sh,enable-https.sh}
  secrets.env.example
  README.md                  the actual deployment runbook
```

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Applied vs. validated | Written and validated (`terraform validate`, `terraform fmt`, `helm lint`, `helm template`) — never `apply`'d or `install`'d against real AWS/Kubernetes | Claiming deployment success without ever running it — would violate this project's own standing honesty rule; see [ADR-0011](adr/0011-terraform-written-not-applied.md) |
| Two Terraform environments | `dev` (EKS, IRSA, managed RDS/ElastiCache — the "how a real team would run this" design) *and* `single-ec2` (one box, SSM-only access, no VPC of its own — the one actually meant to run) | Just one — either loses the genuine Kubernetes-shaped design work, or loses the environment that's actually runnable for a real portfolio deployment |
| Single-EC2 instance size | `t3.large`, chosen from real `docker stats` measurements of this project's own dev stack plus a deliberately-limits-not-idle-based sizing methodology | Guessing a size, or copying a number from elsewhere without re-measuring this project's actual footprint — see [ADR-0012](adr/0012-single-ec2-instance-sizing.md) |
| KEDA autoscaling for enrichment/anomaly, fixed replicas for notification | KEDA's Kafka-lag scaler for the two services with a clean one-topic-per-consumer-group shape; fixed `replicaCount: 2` for notification-service, which consumes two topics under one consumer group (doesn't map onto KEDA's single-trigger-per-topic Kafka scaler cleanly) and fans out to Redis Pub/Sub rather than a downstream topic other services would scale against | CPU-based HPA for the workers — rejected because an I/O-bound consumer waiting on Postgres/OpenAI can have near-zero CPU with a large Kafka backlog; CPU utilization doesn't reflect the actual bottleneck for this shape of service |
| core-api health probes in Helm | `livenessProbe` → `/live`, `readinessProbe` → `/ready` (this project's own real liveness/readiness split, built in an earlier phase) | Both probes hitting the same `/health` endpoint — loses the distinction between "process alive" and "process actually ready to serve real traffic" |
| Worker health+metrics | One `httpPort` serving both `/health` and `/metrics` per worker (matches this project's actual `app/health.py` design from Phase 12) | Two separate mechanisms — a metrics-only port, no health endpoint alongside it |
| CI infra validation | Added: a new `infra-validate` job runs `terraform fmt -check`, `terraform validate` (every module, both envs), `helm lint` (both charts, all three worker values files), and `helm template` (confirms every chart actually renders) on every push/PR | No infra CI at all — a real gap this closes |

## Why infra CI matters here

Terraform and Helm content can be well-designed and still silently rot:
a `terraform.tfvars` typo, a Helm template syntax error, or a values
file drifting out of sync with the chart's schema can sit undetected
indefinitely with nothing catching it. This phase's `infra-validate` CI
job catches exactly that class of bug on every push — the same
"verify, don't just write and assume" discipline this project applies
to application code, now applied to the infrastructure code too.

## Tradeoffs

- No container registry, no CI image-build-and-push job. The `dev`
  Terraform environment's EKS node group and the Helm charts'
  `image.repository` values all assume images exist *somewhere*
  (ECR, most likely) — provisioning that registry and wiring a
  build-and-push job is out of scope for this phase, same honest
  boundary as everything else here that's designed-but-unrun.
- `deploy/docker-compose.prod.yml`'s resource limits for `web` and
  `nginx` are estimated, not measured (they aren't part of the local
  dev compose stack) — flagged explicitly in
  [ADR-0012](adr/0012-single-ec2-instance-sizing.md) rather than
  presented as equivalent to the twelve services that *were* measured.
- `deploy/scripts/backup.sh` writes to the same EBS volume it's backing
  up — no off-box copy by default. Documented as a deliberate, known gap
  in `deploy/README.md` (an `aws s3 cp` step is the natural next
  addition, not implemented here).
- The `infra-validate` CI job's exact behavior in GitHub's own runner
  environment wasn't confirmed by actually pushing and watching it run
  (no CI runner available in this environment) — confirmed
  syntactically valid YAML and logically consistent with the same
  `terraform`/`helm` commands verified locally, same caveat already
  standing for the `chaos-smoke-test` job since Phase 13.

## Verification checklist

- [x] **Terraform**: `terraform init -backend=false` + `terraform validate`
      passed for all 5 modules (`vpc`, `eks`, `rds`, `elasticache`, `iam`)
      and both environments (`dev`, `single-ec2`) — real command output,
      not assumed. `terraform fmt -recursive` run across the whole tree;
      re-`validate`'d clean afterward.
- [x] **Helm**: `helm lint` passed for `core-api`, and for `worker`
      against its base `values.yaml` and all three per-service overrides
      (`values-enrichment.yaml`, `values-anomaly.yaml`,
      `values-notification.yaml`). `helm template` confirmed every
      chart/values combination renders valid manifests — specifically
      confirmed the `worker` chart's KEDA `ScaledObject` appears for
      enrichment/anomaly and correctly does *not* appear for
      notification (fixed-replica design), by grepping the rendered
      `kind:` values.
- [x] **Instance sizing**: real `docker stats --no-stream` measurement
      of all 12 running backend + observability containers in this
      project's own dev stack (≈754 MiB idle, measured) — used to derive
      production resource *limits* (≈4.87 GiB summed) and confirm
      `t3.large` (8 GiB) is the right minimum, not a guess. See
      [ADR-0012](adr/0012-single-ec2-instance-sizing.md) for the full
      table and arithmetic.
- [x] `docker-compose.prod.yml` — YAML structure validated
      (`yaml.safe_load`, confirms all 15 services + 8 named volumes
      parse correctly); **not** validated via `docker compose config`
      against the real stack, since it references
      `/opt/meridian/secrets.env` — a path that only exists on the
      actual target EC2 host, not this development machine. Stated as a
      gap, not silently skipped.
- [x] `.github/workflows/ci.yml` — YAML validated
      (`yaml.safe_load`); the new `infra-validate` job runs the exact
      same `terraform`/`helm` commands already verified locally above.
- [ ] **Not done, by design**: `terraform apply` or `helm install`
      against real AWS/Kubernetes infrastructure. No AWS account,
      credentials, or budget were available to this project for that —
      see [ADR-0011](adr/0011-terraform-written-not-applied.md) for the
      full, explicit accounting of what this means was and wasn't
      checked.
