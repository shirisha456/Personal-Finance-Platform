# Security

A consolidated reference for every security-relevant decision in this
project — most already documented individually across the README and
ADRs; gathered here in one place since "how does auth/encryption/access
control actually work" is exactly the kind of question that shouldn't
require reading fifteen separate documents to answer.

## Authentication

- **Password hashing**: Argon2id via `argon2-cffi`, with explicit
  OWASP-cited parameters (not library defaults — see
  `app/core/security.py`). Verified: hashing the same password twice
  produces different hashes (salted), and the plaintext is never
  recoverable from the stored hash.
- **Access tokens**: JWT, 15-minute expiry, `HS256`, signed with
  `JWT_SECRET`. Kept in memory only on the frontend (Zustand state,
  never `localStorage`/`sessionStorage`) — an XSS payload reading
  browser storage can't exfiltrate it.
- **Refresh tokens**: opaque random tokens, stored server-side only as
  a SHA-256 hash (never the raw token — a leaked database dump can't be
  replayed), delivered via an `httpOnly`, `SameSite=Lax` cookie scoped
  to `/api/v1/auth` (not readable by JavaScript, not sent to unrelated
  paths). 30-day expiry.
- **Rotation + theft detection**: every refresh issues a *new* refresh
  token and invalidates the old one (rotation). If an already-used or
  already-revoked refresh token is ever presented again, the entire
  token family is killed — not just that one token. This is the actual
  signal that a refresh token was stolen and used by someone else
  before or after the legitimate client: a legitimate client would
  never present a token it already knows is stale.
- **Production boot guard**: `Settings.assert_safe_for_environment()`
  refuses to start in `production` if `JWT_SECRET` is still the
  placeholder default — without this guard, nothing would stop a
  misconfigured prod deploy from signing tokens with a value anyone
  reading this repo's history could see.
- **Rate limiting on `/login` and `/register`**: a Redis-backed, per-IP,
  fixed-window limiter (5 requests/min for register, 10/min for login) —
  fails open on a Redis outage rather than locking out the single most
  critical path in the app (ADR-0002) — see
  [ADR-0013](adr/0013-per-ip-fixed-window-rate-limiting.md).

## Authorization

- **Ownership checks are centralized**: `app/core/ownership.py`'s
  `get_owned()` is the single implementation every domain router uses.
  A resource that doesn't exist and a resource that belongs to a
  different user return the *identical* 404 — never 403, never any
  signal that distinguishes "not yours" from "doesn't exist at all."
  Centralizing it here means the guarantee holds everywhere by
  construction, not by each router remembering to hand-roll the same
  check correctly.
- **WebSocket auth**: a long-lived access token in a URL query string
  is a real exposure (server access logs, proxy logs, browser history) —
  browsers can't set a custom header on a WS handshake, so *something*
  has to go in the URL. Instead: `POST /auth/ws-ticket` (requires a
  valid access token) mints a single-use, 30-second ticket; the actual
  `GET /ws/live?ticket=...` redeems it once and it's immediately
  invalid for reuse. Verified: a ticket used twice is rejected the
  second time (`test_ticket_is_single_use`).

## Data protection

- **Plaid access tokens encrypted at rest**: Fernet symmetric
  encryption (`ENCRYPTION_KEY`), a documented local stand-in for real
  AWS KMS envelope encryption — see
  [ADR-0003](adr/0003-local-envelope-encryption-stand-in.md). The real
  KMS key this is meant to become (with rotation enabled, a 30-day
  deletion window, and an IAM policy scoping `kms:Decrypt`/
  `kms:GenerateDataKey` to exactly the IRSA-assumed core-api role) is
  already written as real Terraform in
  `infra/terraform/modules/iam/main.tf` — validated, never applied (see
  [ADR-0011](adr/0011-terraform-written-not-applied.md)).
- **Redis-backed secrets are never the source of truth**: idempotency
  keys, cached responses, and rate-limit counters all fail *open* on a
  Redis outage (proceed as if uncached) rather than blocking the
  request — a deliberate choice for cache-shaped uses specifically
  ([ADR-0002](adr/0002-fail-open-redis-dependencies.md)), explicitly
  *not* applied to WS ticket redemption (`app/core/ws_tickets.py`),
  which fails closed since that's an actual auth guarantee, not a cache.
- **Secrets never committed, never in Terraform state**: every
  `.env.example`/`secrets.env.example` is a template with blank/
  placeholder values; the real production secrets file
  (`/opt/meridian/secrets.env`) is created by hand on the deployment
  host over an SSM session and never touches git or Terraform's state
  file (see `deploy/README.md`).
- **Money is never a float**: every amount is an integer in minor units
  (cents) — eliminates an entire class of floating-point rounding bugs
  in financial arithmetic, at the schema level, not by convention.

## Transport & network

- **CORS**: explicit origin allowlist (`CORS_ORIGINS`), not a wildcard.
- **Production deployment (single-EC2)**: the EC2 instance's security
  group allows inbound 80/443 only — **no SSH port at all**. All
  operator access is via AWS SSM Session Manager (IAM-authenticated,
  fully audit-logged), not a key pair. IMDSv2 is required
  (`http_tokens = "required"`) — closes the classic SSRF-to-instance-
  metadata-credential-theft path.
- **Production deployment (EKS, written not applied)**: RDS and
  ElastiCache security groups only accept traffic from the EKS cluster's
  own security group, never `0.0.0.0/0`; both have encryption at rest
  and in transit enabled by default in the Terraform module
  (`storage_encrypted`, `at_rest_encryption_enabled`,
  `transit_encryption_enabled`).
- **IAM is scoped, not broad**: the single-EC2 instance's IAM role has
  exactly one managed policy attached (`AmazonSSMManagedInstanceCore`)
  — no S3, no Secrets Manager access, because nothing on that box needs
  an AWS API to fetch a secret (they're created by hand instead). The
  EKS path's core-api pod uses IRSA (IAM Roles for Service Accounts) —
  federated, short-lived credentials scoped to exactly the Secrets
  Manager ARNs and KMS key it needs, no long-lived access keys baked
  into an image or a Kubernetes Secret anywhere.
- **HTTPS**: Let's Encrypt via `certbot`, HTTP-01 challenge
  (`deploy/scripts/enable-https.sh`), TLS 1.2/1.3 only. Explicitly
  documented as HTTP-only by default (no domain = no browser-trusted
  cert is possible for a bare IP under CA/Browser Forum rules) — stated
  plainly in `deploy/README.md` as fine for a private demo, not fine for
  real financial credentials.

## Error handling

- One consistent envelope for every failure path —
  `{"error": {"type", "message", "details"}}` — whether the failure is
  a deliberate `AppError` subclass, a Pydantic validation error, an
  unmatched route, or a genuinely unhandled exception. The unhandled-
  exception handler specifically logs the full exception server-side
  but returns only a generic message to the client — internals (queries,
  paths, a stray secret in an f-string) never leak into an HTTP response.

## Known gaps (documented, not hidden)

- **No dead-letter queue** for any of the three Kafka-consuming
  services — a permanently malformed message is logged and skipped, an
  accepted tradeoff for this project's scale (`docs/phase8.md`,
  `docs/phase9.md`), not a resilience gap that was overlooked (chaos-
  tested resilience for the failure modes that *are* handled — see
  `docs/phase13.md`).
- **No Plaid webhook receiver** — sync is user-triggered only, not
  pushed in real time (`docs/phase6.md`).
- **No formal penetration test or third-party security audit** — this
  document describes the security decisions actually made and why, not
  an independent assessment of their sufficiency.
