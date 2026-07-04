# PROGRESS.md — WALoader build status

**Active goal:** `goals/G01-waloader-complete.md` — **COMPLETE** (v0.1.0)
**Current phase:** all phases done (P0–P14)
**Last validation:** 2026-07-03 P14 final: unit 315 passed · integration 2 passed ·
e2e 1 passed (real deployment) · caddy 3 passed (real proxied round-trip) ·
ruff clean · doctor all checks passed (full, with network) · real-browser
verification of the served UI (bootstrap → login → dashboard card → deployed
child app reading its dataset via the SDK)

## Phase checklist

- [x] **P0 Bootstrap** — git repo, .gitignore, uv project (pyproject, src layout, lock),
      pytest/ruff harness, PROGRESS.md, DEVLOG.md, first trivial test green
- [x] **P1 Foundation** — config system + derived paths, logging + Rich, path utils +
      `private/` guard, SQLite layer (WAL), migrations framework + 001_initial,
      domain models, repositories
- [x] **P2 Security & users** — argon2 hashing, WALoader users, login/session service,
      admin bootstrap (first-run screen + create-admin CLI in P8), authorization checks,
      config docs framework with doc-sync tests
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
- [x] **P8 CLI tools** — db, appctl, caddyctl, maintenance, users, serve, doctor
      (backup/archive/retention services pulled forward from P12)
- [x] **P9 WALoader UI core** — login/logout/password change, dashboard + cards,
      create-app screen with availability check, success/error/retry flows, gear modal
- [x] **P10 Dataset & user-management UI** — concepts mapping screen, admin users UI,
      app-owner app users UI, observations/attachments UI
- [x] **P11 Admin panel** — configuration (DB overrides + sources), processes
      (reconcile/resume/maintenance), Caddy panels
- [x] **P12 Backups/retention/maintenance** — background worker (health loop + daily
      jobs in-process), operator triggers, background_enabled setting
- [x] **P13 Documentation** — all guides incl. bundle contract, LLM prompt template,
      sample bundle, manual smoke checklist, README; real e2e deployment test
- [x] **P14 Hardening & final verification** — caddy proxied round-trip e2e,
      no-email-on-stop/restart tests, real-browser UI verification, final DoD pass,
      tag v0.1.0

## Definition of Done (G01 §9) — final status

1. ✅ Every §4 capability implemented with service-layer paths and traceable tests
   (§6 list reviewed; 321 tests across unit/integration/e2e/caddy + AppTest UI suites)
2. ✅ All phases P0–P14 checked off with per-phase commits
3. ✅ On this machine: unit 315 · integration 2 · e2e 1 · caddy 3 — all green; ruff clean
4. ✅ doctor passes (full, network); serve boots; core smoke-checklist flows verified
   in a real browser (bootstrap, login, dashboard card + live health badge, deployed
   app serving its Dataset Concept). A full human walk of
   docs/manual-smoke-checklist.md is recommended before first production use,
   including Caddy-mode browsing.
5. ✅ All docs exist and match behavior; every setting documented (enforced by tests)
6. ✅ argon2 everywhere; redaction tested; `private/` guard tested; no secrets committed
7. ✅ No open blockers; final DEVLOG entry; tagged v0.1.0

## Known limitations (accepted, documented)

- Health checks/maintenance run only while WALoader runs (no cron/systemd by design)
- Streamlit sessions don't survive a full browser refresh (login again)
- The shipped `send_mail` is a logging stub — replace its body at work
- Bundles are text-only; binary data flows through Dataset Concepts

## Known blockers

None.
