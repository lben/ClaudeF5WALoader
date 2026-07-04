# PROGRESS.md — WALoader build status

**Active goal:** `goals/G01-waloader-complete.md` (the only goal)
**Current phase:** P13 — Documentation (in progress)
**Last validation:** 2026-07-03 P12: `uv run pytest` → 314 passed; ruff clean

## Phase checklist

- [x] **P0 Bootstrap** — git repo, .gitignore, uv project (pyproject, src layout, lock),
      pytest/ruff harness, PROGRESS.md, DEVLOG.md, first trivial test green
- [x] **P1 Foundation** — config system + derived paths, logging + Rich, path utils +
      `private/` guard, SQLite layer (WAL), migrations framework + 001_initial,
      domain models, repositories
- [x] **P2 Security & users** — argon2 hashing, WALoader users, login/session service,
      admin bootstrap (CLI + first-run), authorization checks, config docs framework
      (create-admin CLI lands in P8 with the other CLIs; service + first-run path done)
- [x] **P3 App core services** — slug service, bundle parser + safety validator,
      filesystem layout, versioning service, dependency policy validator,
      uv command/env builder + redaction, uv preflight
- [x] **P4 Runtime services** — port allocation, process manager, Caddyfile generation,
      Caddy wrapper, deployment pipeline, create/retry/update orchestration
- [x] **P5 Health & notifications** — health checks, state machine, startup
      reconciliation, notification service + send_mail logging stub, crash detection
- [x] **P6 Dataset Concepts** — concepts CRUD, upload storage (csv/xlsx/xls/parquet,
      Excel sheet names), canonical Parquet, schema inference + diff, replacement
      workflow, waloader_sdk.datasets
- [x] **P7 User management module** — per-app toggle, app users CRUD, observations,
      attachments, login requirement, waloader_sdk.auth
- [x] **P8 CLI tools** — db, appctl, caddyctl, maintenance, users, serve, doctor (P12 service layer — backups, archive, retention — pulled forward; P12 keeps the background thread)
- [x] **P9 WALoader UI core** — login/logout/password change, dashboard + cards,
      create-app screen with availability check, success/error/retry flows, gear modal
- [x] **P10 Dataset & user-management UI** — concepts mapping screen, admin users UI,
      app-owner app users UI, observations/attachments UI
- [x] **P11 Admin panel** — configuration, processes, Caddy panels
- [x] **P12 Backups/retention/maintenance** — backup service, archives, retention
      cleanups, background maintenance thread
- [ ] **P13 Documentation** — all docs incl. bundle contract, LLM prompt template,
      manual smoke checklist, README
- [ ] **P14 Hardening & final verification** — polish, full test review, e2e + doctor +
      manual checklist on this machine, tag v0.1.0

## Active acceptance criteria (P0)

- `uv sync` succeeds; `uv.lock` committed
- `uv run pytest` green (bootstrap test)
- `uv run ruff check .` clean
- git history started with an initial commit

## Known blockers

None.
