# Phase 11 — Frontend

## Goal

A Next.js 16 (App Router) + React 19 dashboard at `web/`: TypeScript,
Tailwind v4, TanStack Query, Zustand, Base UI-flavored shadcn
components, `react-plaid-link`, built directly against the real backend
API surface — every hook and dialog cross-checked against the actual
routers/schemas in `apps/core-api`, not assumed — with every backend
feature that exists (account delete/update, transaction
delete/filtering, price refresh, goal editing) actually wired up to a
real UI control, not left unused.

## Building against the real contracts

Every file under `apps/core-api/app/**/router.py` and `**/schemas.py`
was read before writing the corresponding frontend hook or dialog, and
cross-checked against what the frontend would naturally assume without
that step. That discipline caught concrete mismatches before they ever
became runtime bugs:

| A naive assumption | The actual backend contract | What this frontend does |
|---|---|---|
| List endpoints return a bare array | `useAccounts`/`useTransactions`/`useGoals`/`useHoldings`'s endpoints return `Page[T]` — `{items, total, limit, offset}` (`app/core/pagination.py`) | Hooks unwrap `.items`; list UIs read `total` for pagination (`TransactionsSection` has real prev/next paging) |
| A WebSocket can just carry the long-lived access token in its URL, no reconnect logic needed | The backend requires a single-use, 30-second `?ticket=` minted via `POST /auth/ws-ticket` (`app/notifications/router.py`) — a deliberate exposure-narrowing design, not an accident | The hook mints a fresh ticket on every (re)connection attempt; real reconnect-with-backoff (1s→15s) on drop |
| A holding/watchlist create body might use `security_name` | `HoldingCreate`/`WatchlistCreate` expect `name` | Field named `name` in `use-investments.ts` and both dialogs; verified in the browser that a named holding actually gets its name (`docs/adr` note below) |
| `POST /auth/refresh`'s response includes a `user` field | Confirmed via `app/auth/schemas.py`: `TokenResponse` has no `user` field at all | Real bug this assumption would have caused — caught and fixed, see below |
| Account/transaction/goal management is read-mostly | Real backend endpoints exist for all of it (`PATCH`/`DELETE /accounts/{id}`, `DELETE /transactions/{id}`, `q`/`date_from`/`date_to`/`category_id` filters, `PATCH /goals/{id}`, `POST /investments/prices/refresh`) | All wired up: `/dashboard/accounts` (new page) with delete + institution management, transaction delete + merchant search + real pagination, `AddGoalDialog` doubles as an edit dialog, a "Refresh prices" button |
| Currency is always `"USD"` | `AccountCreate.currency` is a real, independent field | Currency selector added (USD/EUR/GBP/CAD) |
| A generic error string per failure mode is enough (`"Registration failed."`, `"Incorrect email or password."`) | Every error response is `{"error": {"type", "message", "details"}}` | `apiErrorMessage()` helper surfaces the real backend message as a fallback everywhere a mutation can fail |

## Two real bugs found and fixed during this phase

1. **Session lost on every page reload.** Fixing the "no `user` field in
   the refresh response" assumption above (by making `user` optional and
   falling back to the existing store value) created a *new* gap: on a
   fresh page load there's no existing user in the store for that
   fallback to preserve, so `user` stayed `null` forever despite a
   valid refreshed `access_token` — the dashboard's auth gate
   (`!isBootstrapping && !user`) then redirected to `/login` even
   though the session was actually valid. Fixed in
   `bootstrapSession()` (`app/lib/api-client.ts`): do the token refresh
   and, only if the store still has no user, a `GET /auth/me` call,
   and update the store exactly once at the end with both — so
   `isBootstrapping` never goes false with a stale/absent user in
   between. Verified with a real full-page reload (not client-side
   navigation) staying on `/dashboard` instead of bouncing to `/login`.
2. **Closed dialogs/popovers/selects never actually closed.** Base UI
   waits for `Element.getAnimations()` to resolve before unmounting a
   closed popup; with this app's CSS producing no animations to wait
   for, that wait never resolved and closed popups stayed fully
   rendered and interactive — confirmed via direct DOM inspection
   (`document.querySelectorAll('[data-slot="dialog-content"]').length`
   staying `1` well after clicking close). Full writeup and fix in
   [ADR-0009](adr/0009-no-popup-close-animations.md).

## Architecture

```
web/src/lib/
  api-client.ts    axios instance; request interceptor injects the
                     access token; response interceptor does silent
                     401 → refresh → retry with single in-flight-refresh
                     dedup; bootstrapSession() (see bug #1 above)
  auth-store.ts    Zustand — accessToken in memory only (never
                     persisted — XSS-readable storage is the threat
                     this avoids); refresh token is an httpOnly cookie
                     the JS layer never touches
  types.ts         hand-written interfaces mirroring backend response
                     schemas (not codegenerated — small enough surface
                     that keeping them in sync by hand, cross-checked
                     against the schemas directly, was simpler than
                     adding a codegen step)

web/src/hooks/     one file per resource, TanStack Query; mutations
                     invalidate the query keys they affect

web/src/app/
  (auth)/login, (auth)/register   client components, real backend
                                    error messages surfaced
  dashboard/layout.tsx             nav shell, auth gate, logout
  dashboard/{page,accounts,budgets,goals,investments,networth}.tsx

web/src/components/ui/    Base UI-backed primitives (button, card,
                             badge, dialog, input, label, popover,
                             progress, select, table, skeleton) — see
                             ADR-0009 for why none of them animate
web/src/components/dashboard/    feature components (dialogs, sections,
                                    notification bell, net-worth chart)
```

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Popup close/unmount reliability | No CSS animations; `BASE_UI_ANIMATIONS_DISABLED` flag + (for `Select`) a plain React conditional unmount — see [ADR-0009](adr/0009-no-popup-close-animations.md) | Real animate-in/out CSS classes — caused dialogs to never actually close in this app's testing environment |
| WS reconnection | Real reconnect with backoff (1s→15s), a fresh single-use ticket minted on every attempt | No reconnect logic at all — incompatible anyway with the backend's single-use tickets |
| Pagination | Unwrap `Page[T]` at the hook boundary; real prev/next controls on the one list (transactions) exposed to the user at meaningful volume | Treating list responses as bare arrays — would crash (`.map is not a function`) against the real backend |
| Error messages | `apiErrorMessage()` surfaces the backend's real `error.message` as the fallback text everywhere a mutation can fail | Hardcoded generic strings per failure type — loses real diagnostic detail (e.g. *why* a 422 happened) |
| Type inference for the `Select` wrapper | Kept it generic (`Select<Value, Multiple>`) when adding the open-state context, matching Base UI's own `Root.Props<Value, Multiple>` | A non-generic wrapper — compiles, but silently loses `onValueChange`'s value-type inference at every call site (caught by `tsc`, not by eye) |

## Tradeoffs

- No animations on any popup (dialogs, selects, the notification
  popover) — a real, visible polish loss, accepted in exchange for
  popups that reliably close. See ADR-0009's "alternatives considered"
  for what would need re-verifying before reintroducing them.
- `web/src/lib/types.ts` is hand-maintained, not generated from the
  backend's OpenAPI schema — fine at this size, would need revisiting
  if the API surface grows substantially.
- No automated frontend test suite yet (`package.json` has no `test`
  script). All verification this phase is manual, against the real
  running backend and a real browser, documented in the checklist
  below — not unit or integration tests.
- Currency formatting (`money.ts`) is locale-fixed to `en-US` regardless
  of the account's actual currency — correct for USD, cosmetically off
  for others (number/symbol placement). Not revisited since every
  account created during verification used USD.

## Verification checklist

- [x] `npm run build` — clean production build, all 10 routes compiled
      and typechecked
- [x] `tsc --noEmit` — clean
- [x] `eslint .` — clean (also fixed a real `react-hooks/refs` violation
      in `use-live-notifications.ts` — a ref was being written during
      render instead of in an effect)
- [x] **Full manual verification against the real running stack**
      (Postgres/Redis/Redpanda/core-api/notification-service in Docker,
      Next.js dev server, then re-verified against a production
      `next build && next start` server to rule out a dev-only
      explanation for the dialog bug): registered/logged in through the
      real UI; created an account (with currency selector); created and
      deleted a transaction; confirmed transaction search and
      pagination controls hit the real filtered/paginated endpoint;
      created and edited a goal; created an investment-type account and
      a holding (confirmed the `name` field fix and symbol
      uppercasing); confirmed `POST /investments/prices/refresh`'s 503
      surfaces as a real, specific message; recomputed net worth;
      generated a monthly insight and saw the real 422 message when no
      categorized spending existed yet, then the real generated summary
      once it did; opened the notification bell popover; confirmed a
      full page reload stays authenticated (bug #1 above); confirmed
      every dialog, the notification popover, and every `Select`
      actually disappears on close (bug #2 / ADR-0009), including via
      the close button, Escape, and — for `Select` — picking an item
- [ ] Live WebSocket push verified in Phase 10 (backend `notification-service`
      → Redis → `/ws/live`) for the *backend*; this phase confirmed the
      frontend mints tickets and opens a connection with no console
      errors, but a live alert/insight arriving over that exact
      connection while this frontend is open was not re-verified here
      (Phase 10 already proved the pipe end-to-end with a raw
      `websockets` client) — documented as not re-proven with this UI
      specifically, rather than assumed.
