# Phase 0 — Repository Foundation

## Goal

Establish the repository shape, tooling, and process the remaining fifteen
phases build on top of — before any application code exists — so that every
later phase adds to a structure that's already settled rather than
retrofitting one.

## Scope

- Directory layout (monorepo: `apps/`, `services/`, `libs/`, `web/`, `docs/`,
  `infra/`, `observability/`, `chaos/`, `deploy/`, `scripts/` — created as
  each phase introduces real content into them, not as empty placeholders)
- Git configuration files (`.gitignore`, `.gitattributes`, `.editorconfig`)
- Root environment example (`.env.example`)
- Documentation skeleton (README shape, `docs/adr/template.md`, this file)
- CI skeleton (`.github/workflows/ci.yml`)
- Docker Compose skeleton (infrastructure containers only — Postgres, Redis,
  Redpanda; application services are added in the phases that build them)

No application code, database schema, or business logic exists yet — that
starts in Phase 1.

## Design decisions

| Decision | Choice | Rejected alternative |
|---|---|---|
| Directory creation | Create a directory only when a phase has a real file to put in it | Scaffold every top-level directory now with placeholder `.gitkeep` files — adds noise git has to track and delete later for no benefit |
| `.gitignore` env-file rule | Broad `**/.env` glob (with `!**/.env.example` exception) | Enumerate each service's `.env` path explicitly — brittle; a new service's env file could go uncovered until someone remembers to add it |
| Docker Compose scope | Infra-only (Postgres/Redis/Redpanda) in Phase 0 | Full stack (app + all 4 services + observability) upfront — those images/Dockerfiles don't exist yet, so the file would reference nothing runnable |
| Per-phase documentation | A `docs/phaseN.md` per phase (goal, decisions, tradeoffs, verification checklist), linked from the README phase table | A single running changelog — loses the "what did this phase actually decide and why" narrative that makes individual phases legible on their own |
| ADR numbering | Start at `ADR-0001`, four-digit, zero-padded | Continue an arbitrary numbering scheme inherited from elsewhere | 
| Git identity | Set per-repository (`git config` without `--global`) | Rely on global git config |
| Compose project name | Pinned explicitly (`name: personal-finance-platform`) in `docker-compose.yml` | Default (directory-name-derived) project name — can vary silently across clones/CI checkouts with different folder names, and risks colliding with an unrelated local Compose project of the same default name |

## Tradeoffs

Creating directories lazily (only when a phase populates them) means the
top-level tree is incomplete until Phase 14–15. That's an intentional
tradeoff: an empty `infra/terraform/` with no files is not meaningfully
"infrastructure," and an empty directory git can't even track is worse
signal than no directory at all.

## Verification checklist

- [x] `docker compose config` parses `docker-compose.yml` without error
- [x] `.github/workflows/ci.yml` is valid YAML
- [x] Repository-local git identity verified (`git config user.name` /
      `user.email`, not global)
- [x] Remote `origin` points at this project's own GitHub repository
