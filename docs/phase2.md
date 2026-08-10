# Phase 2 — Authentication and Security

## Goal

Registration, login, logout, and a `/me` endpoint, backed by Argon2id
password hashing and rotating refresh tokens with theft/reuse detection —
the first real domain module, and the first real database schema.

## Architecture

```
Client
  → POST /api/v1/auth/register | /login
      → app/auth/service.py (register_user | authenticate_user)
      → Argon2id hash/verify (app/auth/security.py)
      → issue_token_pair: access token (JWT, 15 min) + refresh token
        (opaque, 30 days, only its SHA-256 hash stored)
      → refresh token set as an HttpOnly cookie, scoped to /api/v1/auth

  → POST /api/v1/auth/refresh (reads the cookie)
      → rotate_refresh_token: reused/revoked token → kill the whole
        family and reject; otherwise mark used, issue a new pair under
        the same family_id

  → GET /api/v1/auth/me
      → get_current_user (app/auth/deps.py): Bearer JWT → user
```

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Password hashing | Argon2id, explicit OWASP-cited parameters (`time_cost=2, memory_cost=19456, parallelism=1`) | argon2-cffi's own library defaults — works, but the cost isn't a deliberate choice tied to this app's threat model, just whatever the installed version defaults to |
| Refresh tokens | Opaque `secrets.token_urlsafe(48)`, only the SHA-256 hash persisted, rotating with family-based reuse detection | A refresh JWT — would let a client construct a "valid-looking" refresh token without a DB round trip, which is exactly what rotation/reuse-detection needs to prevent |
| `users` schema | `password_hash` required (`NOT NULL`), no `google_sub` column | Nullable `password_hash` + a unique `google_sub` column — speculative schema scaffolding for Google OAuth that isn't actually implemented anywhere. Dropped as dead scope; add it back in a real phase if OAuth is ever in scope |
| Production boot safety | `Settings.assert_safe_for_environment()` refuses to start with the placeholder `JWT_SECRET` when `ENVIRONMENT=production` | No check — shipped a real, guessable default with nothing stopping a misconfigured deploy from using it |
| Error responses | `ConflictError` / `UnauthorizedError` (from Phase 1's `AppError` hierarchy) for duplicate email and bad credentials; same message for "no such user" and "wrong password" | Distinct messages per failure reason — would let a client enumerate which emails have accounts |
| Password minimum | 8 characters via Pydantic `Field(min_length=8)` | No minimum |

## Tradeoffs

- Argon2id's tuned cost parameters trade hashing latency for resistance to
  offline brute-force; the specific numbers are a starting point (see the
  comment in `app/auth/security.py`) and should be re-benchmarked against
  real deploy hardware, not treated as permanently correct.
- Dropping the OAuth scaffolding means adding Google (or any) OAuth later
  is a real schema migration, not just wiring up an endpoint against an
  already-nullable column. Accepted deliberately — an unused nullable
  column and unique constraint is a worse cost to carry indefinitely than
  a future migration is to write once actually needed.
- No rate limiting on `/login` or `/register` yet — Redis isn't introduced
  until a later phase. This is a real, currently-open gap, tracked here
  rather than silently deferred.

## Extensibility

Any future route that needs to know who's calling adds
`current_user: User = Depends(get_current_user)` — no other module needs
to touch `app/auth/` to consume identity.

## Verification checklist

- [x] `alembic revision --autogenerate` produced a clean `users` +
      `refresh_tokens` migration against real Postgres; `alembic upgrade
      head` applies it with no manual edits needed
- [x] `pytest -v` — 35 tests passing (12 from Phase 1 + 23 new: password
      hashing, JWT round-trip, the production-secret guard, and the full
      auth route surface including the rotate-then-reject-reuse-then-kill-
      the-family flow, tested by presenting an already-rotated-away token)
- [x] `ruff check .` — clean
- [x] End-to-end against real Postgres (not just SQLite): register → 201,
      duplicate register → 409, login → 200, unauthenticated `/me` → 401,
      cookie-based `/refresh` → 200 with a rotated token, `/logout` → 204,
      `/refresh` after logout → 401
- [x] Refresh cookie confirmed `HttpOnly`, `SameSite=lax`, scoped to
      `/api/v1/auth`
- [x] Response schemas confirmed to never include `password_hash`
