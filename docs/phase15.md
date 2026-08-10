# Phase 15 — Portfolio Documentation

## Goal

The last phase, and the only one with no application code changes:
close out the five documentation files every earlier phase deferred
here — `docs/architecture.md`, `docs/api.md`, `docs/security.md`,
`docs/demo.md`, `docs/case-study.md` — plus a final pass over the root
README now that all 16 phases are actually complete. `case-study.md`
specifically needed its own narrative structure decided (problem →
architecture → tradeoffs → results → lessons → what's next), since it
isn't something the rest of this project's per-phase docs already
established a template for.

## What's here

- **`docs/architecture.md`** — Mermaid diagrams: system overview, the
  event-pipeline sequence (one HTTP request → outbox → 4 services, with
  where trace-context propagation actually happens at each hop), the
  auth/refresh/WS-ticket flow, the observability data flow, and both
  deployment topologies.
- **`docs/api.md`** — a curated endpoint map by domain (method, path,
  purpose), explicitly pointing at the live Swagger UI
  (`/docs`/`/openapi.json`) as the authoritative schema reference rather
  than duplicating every request/response model by hand — a
  hand-maintained copy of FastAPI's own generated schema would drift.
- **`docs/security.md`** — every security decision already scattered
  across the README and individual ADRs, gathered into one place:
  authentication, authorization, data protection, transport/network,
  error handling, and known gaps stated plainly (no rate limiting on
  auth endpoints, no dead-letter queue, no third-party security audit).
- **`docs/demo.md`** — a concrete ~10-minute walkthrough script,
  written so someone else could actually follow it: register, trigger
  the categorization pipeline, trigger a live alert, generate an
  insight, find the resulting trace in Tempo, run the chaos tests.
- **`docs/case-study.md`** — the portfolio narrative piece, following
  an 8-section arc (problem → architecture → scalability decisions →
  reliability features → tradeoffs → performance results → lessons
  learned → what's next).

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| `docs/api.md` scope | A curated map (domain, method, path, purpose) pointing at the live OpenAPI docs as the schema authority | Hand-transcribing every request/response schema — guaranteed to drift from the real Pydantic models the instant either changes |
| `case-study.md`'s "Performance Results" section | Only real, measured numbers (test counts, real `docker stats` memory measurements, real chaos-test timings), with an explicit statement that no formal load/stress testing was performed | Fabricating throughput/latency numbers for a denser-looking section — would violate this entire project's standing honesty rule for the sake of a more impressive-looking document |
| Whether to write this phase's own `docs/phase15.md` | Yes, matching the pattern every other phase followed | Skipping it since this phase has no code — rejected for consistency; the established per-phase-doc requirement never carved out a documentation-only exception |

## Verification checklist

- [x] `docs/case-study.md`'s narrative arc (problem → architecture →
      scalability → reliability → tradeoffs → performance → lessons →
      what's next) decided deliberately as this project's own structure
      for that document, then applied directly.
- [x] Every endpoint listed in `docs/api.md` cross-checked against a
      fresh `grep` of every `@router.(get|post|patch|put|delete)`
      decorator across `apps/core-api/app/**/router.py` — not written
      from memory of earlier phases' summaries.
- [x] The ownership-check claim in `docs/security.md` ("a resource that
      doesn't exist and one that belongs to someone else return the
      identical 404") verified against the actual
      `app/core/ownership.py` source, not assumed from an earlier
      phase's description.
- [x] `docs/case-study.md`'s numbers cross-checked against this
      project's own real records: 168 tests (116 + 24 + 17 + 5 + 6,
      matching the exact counts in each phase's own verification
      checklist), ≈754 MiB idle memory (the same measurement
      `docs/adr/0012-single-ec2-instance-sizing.md` used), and the
      0.03–0.06s broker-outage transaction-creation timing (the actual
      output `chaos/test_outbox_broker_outage.py` produced in
      `docs/phase13.md`'s verification).
- [x] Full backend test suite re-run one final time across all 5
      packages after this phase's changes (docs-only, no code touched,
      confirmed nothing regressed) — see the final report for the exact
      count.
- [x] README.md given a final pass: "Future enhancements" replaced with
      a real consolidated list, the intro updated to reflect all 16
      phases being complete, every new doc linked from the appropriate
      section.
