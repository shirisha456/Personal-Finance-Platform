# ADR-0009: No CSS open/close animations on popups (Dialog/Popover/Select)

## Status

Accepted

## Context

The frontend's UI primitives (`web/src/components/ui/dialog.tsx`,
`popover.tsx`, `select.tsx`) are built on `@base-ui/react`. Base UI's own
close behavior doesn't simply toggle CSS — before actually removing a
closed popup from the DOM (or, for `Dialog`, before flipping its native
`hidden` attribute), it calls `Element.getAnimations()` on the popup and
waits for every animation the browser reports to resolve
(`useAnimationsFinished`/`useOpenChangeComplete` in Base UI's internals),
scheduled via `requestAnimationFrame`.

While building out the dialogs, closing any of them — the close button,
Escape, selecting a `Select` item, clicking outside — left the popup
fully present and interactive in the DOM indefinitely. Investigated by
reading Base UI's source directly (`node_modules/@base-ui/react/**`):
the store's `open` state and `data-closed` attribute updated correctly
on every close, but the animation-completion step that's supposed to
follow never resolved. `Element.getAnimations()` reported zero active
animations, `useAnimationsFinished`'s `frame.request(exec)` callback
should fire the next frame regardless — yet the popup stayed mounted and
fully opaque (with no CSS classes making a `data-closed` popup
transparent, this wasn't just a leftover invisible DOM node — it was
still visible, on top of whatever the user clicked next). Reproduced
identically in `next dev` and a full `next build && next start`
production server, ruling out a React Strict Mode double-effect
explanation. The one environment where `requestAnimationFrame` is
documented to be throttled or not fire — a background/non-compositing
browser tab — matches the automated testing setup used to verify this
phase, but the fix below doesn't depend on that being the full
explanation, only on Base UI's animation-frame-scheduled completion path
being unreliable somewhere in this stack.

## Decision

1. **No `animate-in`/`animate-out` CSS classes on any popup** —
   `DialogOverlay`, `DialogContent`, `PopoverContent`, and
   `SelectContent` render with plain, static styling only.
2. **`globalThis.BASE_UI_ANIMATIONS_DISABLED = true`**, set once in
   `web/src/lib/providers.tsx` before any Base UI component mounts —
   this is a flag Base UI's own `useAnimationsFinished` checks
   (`node_modules/@base-ui/react/internals/useAnimationsFinished.js`)
   to skip the `getAnimations()`/animation-frame wait entirely and
   resolve synchronously. This alone was enough to fix `Dialog`.
3. **`Select` additionally unmounts via a plain React conditional**, not
   just the flag above — `SelectContent` is only rendered while `open`
   is true, gated through a small context the `Select` wrapper provides
   (`web/src/components/ui/select.tsx`). This was necessary because
   `SelectRoot`'s own completion hook is `enabled: !actionsRef`
   (`node_modules/@base-ui/react/select/root/SelectRoot.js`) — a
   different code path than `Dialog`'s, and the flag alone didn't
   resolve it in testing. The `Select` wrapper's `open` is
   controlled-or-uncontrolled (accepts external `open`/`onOpenChange`,
   falls back to internal `useState`), so this required no changes at
   any of the seven call sites already using `<Select>`.
4. **The four `Add*Dialog` components and `NotificationBell`'s popover
   also conditionally render their content on `open`** (found and fixed
   before the flag, kept as-is rather than reverted — a second,
   independent guarantee that costs nothing once already written).

## Alternatives considered

- **Debug and fix Base UI's animation-completion detection itself** —
  rejected as out of scope: this is third-party library internals, not
  application code, and the flag Base UI itself exposes for exactly
  this situation is the supported way to opt out, not a workaround.
- **Keep the animations, accept the stuck-open bug as a known
  limitation** — rejected outright. This wasn't a cosmetic issue: a
  dialog that never closes blocks the page underneath it. Not a
  defensible tradeoff for basic functionality.
- **Add real CSS animations back later, once confirmed reliable in a
  normal (non-automated, foreground) browser tab** — deferred, not
  rejected. If a future phase revisits this, the fix should be
  re-verified with a real visible browser session (this project's
  testing setup couldn't rule out the non-compositing-tab explanation
  with certainty), not assumed safe by default.

## Consequences

- Dialogs, popovers, and selects open/close instantly with no
  transition — a real, deliberate loss of polish, in exchange for
  closing actually working.
- Any future addition to `components/ui/` that wraps a Base UI popup
  primitive should follow the same pattern (no animate classes, and if
  it's a new primitive type beyond Dialog/Popover/Select, verify its
  close behavior explicitly rather than assuming the global flag
  covers it — `Select`'s `enabled: !actionsRef` path shows the flag
  doesn't uniformly reach every component's completion hook).

## Validation

Manually verified against the real running app (`next dev`, then again
against a full production build via `next build && next start`, to
rule out the build-mode being the reason it worked) via direct DOM
inspection: opened and closed `AddAccountDialog`, confirmed
`document.querySelectorAll('[data-slot="dialog-content"]').length`
dropped to `0` immediately after clicking the close button (previously
stayed `1` indefinitely). Repeated for `Select` inside
`AddTransactionDialog` (selecting an item now removes
`[data-slot="select-content"]` from the DOM immediately, and the
trigger correctly displays the selected item's label rather than its
raw id — a related, separately-fixed bug, see `docs/phase11.md`).
Repeated for `NotificationBell`'s `Popover` via Escape-key close.
