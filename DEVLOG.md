# DEVLOG.md — append-only implementation log

## 2026-07-03 — P0 Bootstrap — complete

- **Summary:** Initialized git repo (`main`), uv project with src layout
  (`waloader`, `waloader_sdk`), full stack pinned in `pyproject.toml`
  (streamlit/pandas/duckdb/plotly/pyarrow/openpyxl/xlrd/pydantic/psutil/rich/
  argon2-cffi/packaging; dev: pytest/ruff), pytest markers
  (`integration`/`e2e`/`caddy`, deselected by default so the default suite is
  offline), ruff config, `.gitignore` protecting `data/`, `private/`, and local
  config, `.python-version` = 3.12.
- **Files changed:** .gitignore, pyproject.toml, .python-version,
  src/waloader/__init__.py, src/waloader_sdk/__init__.py,
  tests/test_bootstrap.py, PROGRESS.md, DEVLOG.md, uv.lock.
- **Tests added:** tests/test_bootstrap.py (imports + version).
- **Validation:** `uv sync` OK; `uv run pytest` → 1 passed; `uv run ruff check .`
  → clean.
- **Known issues:** none.
- **Next:** P1 Foundation (config system, logging, path utils + private/ guard,
  SQLite layer, migrations, models, repositories).

## 2026-07-03 — P1 Foundation — complete

- **Summary:** Config system (pydantic, TOML + $WALOADER_CONFIG discovery,
  extra="forbid" so undocumented settings hard-fail, derived paths from
  paths.data_dir, DB-override overlay with per-key source tracking, paths.*
  excluded from DB edits); path utilities with repo-local `private/` guard
  (macOS system /private explicitly exempt) and safe_join bundle-path guard;
  logging (rotating app/error logs + Rich tracebacks); SQLite layer (WAL,
  busy_timeout 5000, FKs ON) with NNN_name.sql migrations framework +
  001_initial full schema (14 tables); dataclass models; repositories for
  users, apps, versions, deployments, runtime, datasets, app_users, settings,
  notifications, approvals, audit.
- **Files changed:** src/waloader/{util,config,paths,logging_setup,db,models}.py,
  src/waloader/migrations/001_initial.sql, src/waloader/repositories/*,
  tests/{conftest,test_config,test_paths,test_db_migrations,test_repositories}.py
- **Validation:** `uv run pytest` → 51 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P2 Security & users (argon2, sessions, admin bootstrap,
  authorization, config docs framework).

## 2026-07-03 — P2 Security & users — complete

- **Summary:** argon2 password hashing + strength check; users_service
  (create/authenticate/change_password/admin_reset/set_active, audit-logged,
  bootstrap_admin only-when-empty); authorization (require_admin,
  can_manage_app/require_app_manager); config docs framework:
  config/waloader.example.toml (every setting, commented) and
  docs/configuration.md (full per-setting reference). Added doc-sync tests: the
  example TOML must cover exactly the config model's key set with default
  values, and configuration.md must mention every setting — undocumented
  settings now fail CI, not just review.
- **Files changed:** src/waloader/services/{__init__,security,users_service,
  authorization}.py, config/waloader.example.toml, docs/configuration.md,
  tests/test_security_users.py
- **Validation:** `uv run pytest` → 67 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P3 App core services (slugs, bundle parser/validator, layout,
  versioning, dependency policy, uv builder + redaction, preflight).

## 2026-07-03 — P3 App core services — complete

- **Summary:** slug service (slugify + reserved names + live availability with
  exclude-self for edits); markdown bundle parser implementing the exact G01
  §4.4 contract (first-fence metadata block 'toml waloader-bundle', '## file:'
  sections, CommonMark longer-fence nesting, every rejection rule incl. raw
  '.'/'..'/empty segments, private/, hidden allowlist .streamlit + .gitignore,
  size/UTF-8 byte boundary); filesystem layout service (DB stores POSIX paths
  relative to data_dir; resolve/relativize at edges); versioning service
  (source tree + manifest.json with sha256s + byte-exact uploaded_bundle.md);
  dependency policy validator (PEP 508 classify url/vcs/path, all 5 flags,
  approval mode, copyable violations block); uv command/env builder
  (UV_CONFIG_FILE/UV_CACHE_DIR/UV_SYSTEM_CERTS/SSL_*, --python,
  --allow-insecure-host, credential redaction) and preflight service
  (dedicated venv + dry-run resolve, injectable runner, redacted output).
- **Validation:** `uv run pytest` → 138 passed; `uv run pytest -m integration`
  → 1 passed (real uv against PyPI on this Mac); `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P4 Runtime services (ports, process manager, Caddy, deployment
  pipeline, orchestration).

## 2026-07-03 — P4 Runtime services — complete

- **Summary:** State machine (8 states, explicit transition map, enforced via
  states.transition with fresh re-read); port allocation (BEGIN IMMEDIATE
  transaction, DB reservation + real bind test, stable reuse incl. the
  own-process-occupies-port update case, exhaustion error); process manager
  (detached spawn POSIX/Windows, pid+create_time identity, psutil tree
  terminate->kill, child command with per-version venv + streamlit flags +
  bind-address auto + baseUrlPath, WALOADER_* env injection with PYTHONPATH
  prepend); Caddy service (DB-generated Caddyfile with /waloader + /apps/*
  routes, validate/start/stop/reload/refresh/status, pidfile with
  create_time); deployment pipeline (parse -> version -> policy -> per-version
  venv + install [streamlit + sdk deps auto-added] -> app tests -> port ->
  swap -> caddy -> initial health poll; pre-swap failures leave the old
  version running, post-swap failures -> deployment_failed with runtime.log
  tail; per-step redacted copyable error block; old-venv cleanup) and
  create/redeploy orchestration with derived retry kinds.
- **Design note:** venvs are per-version (runtime/venvs/000001) so an update
  never mutates the environment of the live old process; prior venvs are
  removed after a successful swap.
- **Validation:** `uv run pytest` → 186 passed; `uv run pytest -m caddy` →
  1 passed (real caddy v2.11.4 validated the generated Caddyfile);
  `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P5 Health & notifications.

## 2026-07-03 — P5 Health & notifications — complete

- **Summary:** Health check service (process->port->HTTP /_stcore/health
  probes; dead process fails immediately, transient unhealthiness needs the
  configured consecutive threshold; running->failed transition triggers crash
  notification rules); notifications module: send_mail STUB in
  notifications/mailer.py logging subject/recipients only (docstring says
  "replace this body at work" — exact corporate signature), crash service
  enforcing deployed_healthy + grace period + enabled flag + per-failure-event
  dedupe (cleared on successful deploy), Outlook-compatible escaped HTML,
  owner + admin_cc recipients; lifecycle service (start/resume with port
  revalidation + health poll + caddy refresh on port change, stop [never
  emails: goes to stopped], restart); startup reconciliation (dead running ->
  stopped + resume candidate + port-conflict warnings, alive stopped ->
  adopted, resume_apps admin action, apps_overview snapshot).
- **Validation:** `uv run pytest` → 212 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P6 Dataset Concepts.


## 2026-07-03 — P6 Dataset Concepts — complete

- **Summary:** datasets_service (concept CRUD with name validation + disk
  cleanup on delete; upload storage for csv/xlsx/xls/parquet with extension +
  size limits; Excel sheet REQUIRED for .xlsx/.xls with available-sheets error,
  ignored/never stored for csv/parquet; originals preserved timestamped +
  canonical current.parquet written per G01 §3.5; schema inference at upload;
  reusable diff_schemas with added/removed/changed + copyable format();
  replacement_diff comparing stored schema [its stored sheet] vs incoming
  [declared sheet]); waloader_sdk.datasets (env-context via WALOADER_*,
  load_dataset -> DataFrame | None | required raise, UnknownConceptError,
  helpful out-of-WALoader error, no_data_placeholder italic empty state);
  waloader_sdk._context shared SDK env resolution. Added dev-only xlwt for
  .xls test fixtures (xlrd reads them in prod code). Note: dtype names differ
  across pandas versions ("object" vs "str") — tests derive expectations from
  pandas itself.
- **Validation:** `uv run pytest` → 240 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P7 User management module.

## 2026-07-03 — P7 User management module — complete

- **Summary:** app_users_service (per-app enable/disable toggle with audit;
  create/update/deactivate/reactivate/delete app users with username+password
  validation, per-app uniqueness, cross-app reuse allowed; authenticate with
  inactive guard; self change-password + owner reset; observations field;
  attachments stored under user_files/<id>/ with no-overwrite naming, metadata
  rows, disk cleanup on delete); waloader_sdk.auth (standalone in child venvs:
  pure core login_required/authenticate/change_password over the shared DB
  with argon2 verify — hash-compatible with the platform service both ways —
  plus streamlit-facing require_login [no-op when disabled, session_state
  login form + st.stop gate], logout_button, change_password_form).
- **Validation:** `uv run pytest` → 258 passed; `uv run ruff check .` → clean.
- **Known issues:** streamlit-facing SDK helpers are covered by the manual
  smoke checklist (P13/P14), pure cores are unit-tested.
- **Next:** P8 CLI tools.

## 2026-07-03 — P8 CLI tools — complete

- **Summary:** Pulled P12's service layer forward (bottom-up rule): backups
  service (sqlite backup API snapshot, sha256 change detection with sidecar,
  age-based backup + log cleanup with empty-dir pruning), deletion service
  (soft delete -> stop, zip archive of versions/datasets/user_files +
  metadata.json [venvs excluded], purge_after stamp, app dir freed, caddy
  route refresh, slug stays reserved; hard_delete_expired purges archive +
  logs + row, freeing slug/port), maintenance_service.run_all. CLIs (all thin
  service wrappers): db, appctl, caddyctl, maintenance, users, serve
  (config-derived streamlit launch + startup reconcile + --print-command),
  doctor (platform/config/binaries/data-dir/db/ports/caddy/preflight checks,
  --offline, exit codes). Shared _common bootstrap = config + DB overrides +
  migrations + logging. ui/app.py is a documented placeholder replaced in P9.
- **Validation:** `uv run pytest` → 283 passed; `-m "integration or caddy"` →
  4 passed (real uv preflight, real caddy validate x2, full doctor with
  network); ruff clean. doctor --offline passes on this Mac.
- **Known issues:** none.
- **Next:** P9 WALoader UI core.

## 2026-07-03 — P9 WALoader UI core — complete

- **Summary:** Real Streamlit UI replacing the P8 placeholder. ui/common
  (fresh effective config per rerun, per-action DB connections, cache_resource
  boot_once = migrations + startup reconciliation, auth gate with first-run
  bootstrap-admin screen then login form, shared deploy-outcome panel: success
  with copyable URL code block + Open-app link, error with copyable
  concatenated error block + fixed-bundle retry uploader). ui/app.py entry
  (sidebar identity + logout, st.navigation with Dashboard/Create/Account).
  Dashboard page: bordered app cards in a 3-column scrollable grid (state
  badge, version/port/description, last-healthy, failure reason, Open button),
  admin "show all apps" toggle, gear dialog per card with: update-code
  uploader (same pipeline), Users Management Support toggle, and
  stop/resume/restart/delete each behind an explicit confirm step. Create
  page: live name availability with ✅/❌ and the smaller italic taken-message,
  description, user-mgmt toggle, bundle uploader, guarded submit + spinner.
  Account page: password change + logout. Tests use streamlit's official
  headless AppTest (script-level, not browser automation): bootstrap flow,
  login wrong/right, dashboard empty state + cards + ownership filtering +
  boot-time reconciliation of dead-running apps, create-page availability
  feedback (available/taken/reserved), account password change/mismatch.
  Gotcha: AppTest.from_function needs self-contained sources (inner imports);
  st.navigation pages tested via from_function wrappers.
- **Validation:** `uv run pytest` → 294 passed; `uv run ruff check .` → clean.
- **Known issues:** gear-dialog flows (dialog interactions) are covered by the
  manual smoke checklist; services underneath are fully unit-tested.
- **Next:** P10 Dataset & user-management UI.

## 2026-07-03 — P10 Dataset & user-management UI — complete

- **Summary:** Datasets page (app selector; add-concept form with validation
  errors; per-concept card with italic "No data uploaded yet" empty state,
  current-file summary + schema expander, delete-with-confirmation; uploader
  per concept with Excel sheet-name input prefilled Sheet1 and required; first
  upload stores directly, replacements auto-show the schema diff in a copyable
  block with "a mismatch may be fine if the app code changed" note and require
  explicit confirmation). App users page (per-app toggle for Users Management
  Support, create-user form, per-user panels: email/observations edit,
  deactivate/reactivate, delete-with-confirm incl. files, owner password
  reset, attachments list/download/remove/add with notes). Admin-only
  WALoader users page (create platform users incl. admins,
  deactivate/reactivate others, password reset; self-deactivation blocked).
  Navigation now has Apps/You/Admin sections; gear dialog points at the real
  pages. AppTest coverage for all three pages.
- **Gotchas fixed:** AppTest flattens widgets column-wise, so tests select
  text inputs by LABEL (positional indices silently hit the wrong fields —
  one earlier assertion passed for the wrong reason and now also verifies
  email + argon2-authenticates the created password); toggle handlers now use
  the service's returned App instead of st.rerun() (rerun looped under
  AppTest).
- **Validation:** `uv run pytest` → 305 passed; `uv run ruff check .` → clean.
- **Known issues:** file-upload widget flows aren't drivable by AppTest;
  covered by service tests + manual smoke checklist.
- **Next:** P11 Admin panel.

## 2026-07-03 — P11 Admin panel — complete

- **Summary:** Configuration panel (every editable section as a form with
  type-appropriate widgets — toggle/number/text/JSON-list — each showing
  source + default in help; whole-overlay validation via apply_db_overrides
  BEFORE persisting so an invalid save changes nothing; per-section
  clear-overrides; [paths] read-only with the derived-paths table and config
  file location; restart-needed caveat surfaced). Processes panel (overview
  dataframe, run-reconciliation with actions/warnings report, resume
  selected/all with per-app results). Caddy panel (status line incl. routes
  count + binary/config paths, generate/validate/start/stop/reload buttons
  with result surface, generated Caddyfile viewer, caddy.log/access.log
  tails, direct-port-mode hint when disabled). Admin section in navigation
  now: Configuration, Processes, Caddy, WALoader users. AppTest coverage:
  non-admin blocked, setting edit persists as DB override + effective config
  reflects it + source flips to "db", invalid port range rejected without
  saving, paths not editable, reconcile flow with resume candidate, caddy
  status/generate.
- **Validation:** `uv run pytest` → 311 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P12 background maintenance thread + operator triggers (services
  already landed in P8).

## 2026-07-03 — P12 Backups/retention/maintenance — complete

- **Summary:** Background worker (daemon thread in the UI process, started
  once via boot_once/cache_resource singleton): each tick health-checks all
  running apps (failures logged, crash-email pipeline engaged) and runs the
  full daily maintenance (change-detected DB backup, backup/log retention
  cleanup, expired-app hard delete) once per UTC day; interval re-read from
  effective config each loop; exceptions logged and survived. New documented
  setting health.background_enabled (default true; false = CLI/admin-panel
  only) — example TOML + configuration.md + doc-sync tests updated. Admin
  Processes page gained a Maintenance section (backup-now, run-full-
  maintenance-now with report). UI test fixtures disable the worker for
  determinism. Services themselves landed in P8; this phase added the
  scheduler + operator surface.
- **Validation:** `uv run pytest` → 314 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** P13 Documentation.

## 2026-07-03 — P13 Documentation — complete

- **Summary:** All docs written and current: markdown-bundle-contract (exact
  format incl. nested-fence rule and rejection list), llm-bundle-prompt
  (copy-paste template for finance users' coding LLMs, with standalone-
  fallback SDK patterns), dataset-concepts-contract, process-management,
  caddy-reverse-proxy (incl. behavior matrix), user-management (incl. the
  send_mail stub replacement instructions + crash-email rules),
  dependency-policy, troubleshooting (incl. RHEL deployment runbook),
  manual-smoke-checklist, README (quickstart bash+PowerShell, docs index,
  trust model). examples/sample-bundle.md added — a working bundle
  demonstrating metadata, nested 4-backtick fences, pyproject, tests,
  .streamlit + .gitignore allowlist, SDK usage with local fallback; it
  doubles as the e2e fixture. NEW tests/test_e2e.py (marker e2e): real
  create→deploy (uv install, detached streamlit, app's own tests ran)→HTTP
  health→dataset upload+replacement diff→update to v2 on same port with venv
  cleanup→stop/resume→health-service check→soft delete+archive→clean
  reconcile. Passed on this Mac in ~13s (warm uv cache reused via
  `uv cache dir`).
- **Validation:** unit 314 passed; `-m e2e` 1 passed; `-m "integration or
  caddy"` 4 passed; ruff clean.
- **Known issues:** none.
- **Next:** P14 hardening & final verification (doctor, serve + manual
  checklist, DoD closure, v0.1.0 tag).

## 2026-07-04 — P14 Hardening & final verification — complete — GOAL DONE

- **Summary:** Coverage-gap review against G01 §6 added two missing pieces:
  tests/test_e2e_caddy.py (caddy marker: REAL proxied round-trip — deploy the
  sample bundle with caddy.enabled, start a real Caddy on :48080, hit the
  app's health endpoint and page THROUGH the proxy, verify the 404 handler
  and hot reload, clean stop) and an explicit test that user stop/restart
  never send crash emails. Real-browser verification of the served UI via the
  preview harness: first-run bootstrap created the admin and auto-logged-in;
  post-refresh login worked; dashboard rendered the deployed sample app card
  (🟢 running · v1 · port 8601 · live last-healthy timestamp from the
  background worker); the child app itself served at :8601 with title
  "Client Positions" and metric "Clients: 3" — i.e. it read its uploaded
  Dataset Concept through waloader_sdk -> canonical parquet. Local
  verification data wiped afterwards; .claude/launch.json added for future
  preview runs.
- **Final validation (this Mac):** unit 315 passed · `-m integration` 2
  passed · `-m e2e` 1 passed (real deployment round-trip ~14s) · `-m caddy`
  3 passed (incl. proxied round-trip) · `uv run ruff check .` clean ·
  `doctor` full run all checks passed. PROGRESS.md carries the DoD closure
  and the accepted-limitations list.
- **Known issues:** none. Recommended before first production use: a human
  walk of docs/manual-smoke-checklist.md (file-upload widgets and Caddy-mode
  browsing can't be fully automated), and at work: replace the send_mail stub
  body per docs/user-management.md.
- **Next:** none — goal complete. Tagged v0.1.0.

## 2026-07-05 — G02 Q0 Archive foundation — complete

- **Summary:** services/app_archive.py: shared format-2 builder — metadata now
  carries owner_username, versions, dataset concepts + current-file rows, app
  users (portable argon2 hashes) + attachments, deployments (reference), with
  include_data switch (code-only excludes datasets/user_files) and runtime/
  (venvs) never archived; read_metadata validates format with clear errors
  (old format-less delete archives are politely rejected). deletion.py now
  uses the shared builder — soft-delete archives are importable going forward.
  services/scoped_backups.py: create_backup scopes all/db/apps/app (app scope
  delegates to the format-2 builder → importable), consistent DB snapshot via
  the sqlite backup API inside the zip, all-scope tree walk excluding
  backups//tmp//uv-cache//venvs and raw DB files, logs opt-in; manifest with
  effective-config dump + apps inventory; filesystem-only registry
  (list_backups manual+factory with factory purge dates, delete_backup with
  traversal guard, cleanup_factory_backups age-pruning). New documented
  setting retention.factory_reset_backup_days=183 (example TOML +
  configuration.md; doc-sync green).
- **Validation:** `uv run pytest` → 329 passed; `uv run ruff check .` → clean.
- **Known issues:** none.
- **Next:** Q1 restore & rebuild.

## 2026-07-05 — G02 Q1 Restore & rebuild — complete

- **Summary:** services/restore.py: wipe_data_dir (shared by restore --force
  and factory reset; removes everything under data_dir except backups/,
  reports leftovers instead of silently ignoring); restore_all (all-scope
  manifest validation with clear errors incl. per-app-archive redirect,
  refuses over an existing DB without force, zip-slip guard, migrates the
  restored DB [archives may predate newer migrations], normalizes
  running/deploying -> stopped + clears pids, reports rebuild-required
  slugs). deployment.rebuild_app: replays the pipeline on the preserved
  byte-exact bundle (kind="rebuild"; appends a new version, honestly);
  needs_rebuild helper. lifecycle.start now refuses with "rebuild required:
  appctl rebuild <slug>" when the current venv is missing (existing lifecycle
  tests updated to create venv markers — the check is real behavior).
  appctl rebuild <slug>|--all. NOTE: services/app_migration.py +
  appctl export/import handlers landed here too (service-before-client for
  the shared parser); their unit tests are Q2's boundary. e2e: real
  backup→wipe→restore→refused-start→rebuild→HTTP-healthy round trip passed,
  plus import round trips already green.
- **Validation:** `uv run pytest` → 341 passed; `-m e2e` → 4 passed; ruff clean.
- **Known issues:** none.
- **Next:** Q2 export/import unit tests.

## 2026-07-05 — G02 Q2 Export/import — complete

- **Summary:** Unit coverage for the migration service that landed in Q1:
  export to backups/manual (code-only flag verified); import fidelity without
  deploy (rewritten version/dataset/attachment paths resolve under the new
  slug, bundles byte-exact, parquet loadable, argon2 hashes authenticate,
  inactive users preserved, observations kept, state=created +
  needs_rebuild); import with deploy seams -> running via kind="rebuild";
  code-only archives create concepts without file rows and skip attachments;
  validation: name collision hints --name (soft-delete reservation), owner
  resolution (archived-owner-exists / missing -> "pass --owner" / unknown
  explicit owner), scope backups redirected to 'backupctl restore' (fixed:
  the probe now reads manifest.json when metadata.json is absent so users get
  the redirect instead of a generic error), zip-slip guard. appctl
  export/import CLI happy path + failure exit codes. e2e import round trips
  were already green in Q1.
- **Validation:** `uv run pytest` → 352 passed; ruff clean.
- **Known issues:** none.
- **Next:** Q3 factory reset + backupctl CLI.
