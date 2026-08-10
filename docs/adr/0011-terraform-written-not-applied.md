# ADR-0011: `infra/terraform/` and `infra/helm/` are written and validated, not applied

## Status

Accepted

## Context

Phase 14 adds a complete infrastructure-as-code tree: two Terraform
environments (`infra/terraform/envs/dev` — VPC/EKS/RDS/ElastiCache/IAM
for a Kubernetes-based deployment, and `infra/terraform/envs/single-ec2`
— a single EC2 instance for an actual portfolio deployment) and two Helm
charts (`infra/helm/core-api`, `infra/helm/worker`) meant to run on the
`dev` environment's EKS cluster.

None of this has been run against real AWS infrastructure. This ADR
states that plainly and explains what "written but not applied" actually
means here, rather than leaving it ambiguous.

## Decision

`infra/terraform/` and `infra/helm/` are complete, reviewable
infrastructure-as-code. They have been validated with:

- `terraform init -backend=false` + `terraform validate` — every module
  (`vpc`, `eks`, `rds`, `elasticache`, `iam`) and both environments
  parse, type-check, and reference their inputs/outputs correctly.
- `terraform fmt -recursive` — canonically formatted.
- `helm lint` — both charts, and the `worker` chart against all three
  per-service values overrides (`values-enrichment.yaml`,
  `values-anomaly.yaml`, `values-notification.yaml`).
- `helm template` — confirmed every chart renders valid Kubernetes
  manifests, including the conditional paths (the `worker` chart's KEDA
  `ScaledObject` correctly appears for enrichment-service/anomaly-service
  and correctly does *not* appear for notification-service, which uses a
  fixed replica count instead).

**None of this has been `terraform apply`'d or `helm install`'d against
a real cluster**, because doing so would need an AWS account, would cost
real money, and this project has neither an assigned budget nor
credentials to use for that. This ADR is the explicit, permanent record
of that boundary — not a gap being quietly glossed over.

## What "validated but not applied" can't tell you

Being honest about the boundary means being specific about what's
actually unverified:

- Whether the AWS account this ever runs in actually has the service
  quotas, IAM permissions, and region availability every resource here
  assumes (VPC quota, EKS cluster limits, EIP limits, etc.).
- Whether the IRSA policy in `modules/iam` is actually *sufficient* at
  runtime — `terraform validate` checks the policy document is
  well-formed JSON with valid actions/resources, not that core-api's
  pod can actually call every AWS API it needs to at runtime.
- Whether the security group rules in `modules/rds`/`modules/elasticache`
  actually permit the right traffic once real EKS node security groups
  exist — the dev environment's `data.aws_eks_cluster.this` re-read
  pattern is architecturally correct but has never resolved against a
  real cluster.
- Whether the EKS managed node group can actually pull images from
  wherever they end up hosted (this repo has no CI image-build-and-push
  step either — see `docs/phase14.md`'s CI section for that scope
  boundary too).
- Any real cost — no `terraform plan`-based cost estimate was ever run
  against either environment.

## Why two environments exist

The `dev` (EKS) environment is the "how would a real team actually run
this" answer — a genuine, non-trivial infrastructure design (managed
node groups not Fargate, IRSA not baked-in credentials, a real RDS
instance not a container, KEDA-based Kafka-lag autoscaling for the
worker services) worth having as reviewable code even though it's never
been applied. The `single-ec2` environment is the one path actually
*intended* to be run for a real, working portfolio deployment — see
[`deploy/README.md`](../../deploy/README.md) for that runbook, and
[ADR-0012](0012-single-ec2-instance-sizing.md) for how its instance size
was chosen (from real measurements, not a guess).

## Alternatives considered

- **Don't write the EKS/Helm path at all, only single-ec2** — rejected.
  The Kubernetes-shaped design (IRSA, KEDA lag-based autoscaling, a
  proper managed RDS/ElastiCache) is worth demonstrating even unapplied;
  cutting it would lose real design work for no honesty gain, since the
  single-ec2 path is *already* clearly marked as the one that's meant to
  actually run.
- **Claim it was tested via `terraform plan` against real credentials
  temporarily** — not done; this project was never given AWS credentials
  to test with, and claiming otherwise would violate its own standing
  rule to never claim something works when it hasn't actually been run.

## Consequences

- Anyone (including a future me) picking this up to actually deploy the
  `dev` EKS environment should expect a real debugging pass —
  `terraform apply` will likely surface issues static validation
  couldn't catch, per the list above.
- The `single-ec2` environment is much more likely to apply cleanly on
  a first attempt — its resource surface is small (one EC2 instance,
  one SG, one IAM role, an EIP, a budget alarm, optionally a Route 53
  zone/record) and every piece was written against AWS APIs that don't
  change shape often.
- If this project ever gets real AWS credentials and a budget, the
  correct next step is `terraform plan` against `single-ec2` first (it's
  the cheap, low-risk path), read the plan output carefully, and only
  then `apply` — not to touch `dev` without a real reason to run
  Kubernetes specifically.

## Validation

See the bullet list under "Decision" above — every command listed there
was actually run against this repository's `infra/` tree, with real
output (not assumed), during Phase 14. See `docs/phase14.md`'s
verification checklist for the exact commands and their results.
