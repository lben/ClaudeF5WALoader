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

## 2026-07-05 — G02 Q3 Factory reset + backupctl — complete

- **Summary:** services/factory_reset.py: stop every child (all states) →
  audit row BEFORE the snapshot (the event travels inside the backup, proven
  by test) → full all-scope backup WITH logs into backups/factory/ (purge
  date = now + retention.factory_reset_backup_days) → caddy stop → wipe
  everything except backups/ → report with undo instructions
  (backupctl restore + appctl rebuild --all) and "restart serve → first-run"
  note; --skip-backup honored; missing-DB degrades to wipe-only with a note.
  tools/backupctl.py: create (--scope all|db|apps|app, --code-only,
  --with-logs), list (kind/scope/size/purge table), restore (--force),
  factory-reset (typed-RESET prompt, EOF-safe non-tty message, --force for
  scripts) — deliberately does NOT use the shared bootstrap so restore into
  an empty data dir never auto-creates a DB first (tested). Maintenance
  integration: run_all + cleanup-retention now prune expired factory backups
  (manual backups never pruned), report/summary extended.
- **Validation:** `uv run pytest` → 362 passed; ruff clean.
- **Known issues:** none.
- **Next:** Q4 UI (Backups & reset admin page, gear export/rebuild).

## 2026-07-05 — G02 Q3 Factory reset + backupctl — complete

- **Summary:** services/factory_reset.py: stop every child (all states) →
  audit row BEFORE the snapshot (the event travels inside the backup, proven
  by test) → full all-scope backup WITH logs into backups/factory/ (purge
  date = now + retention.factory_reset_backup_days) → caddy stop → wipe
  everything except backups/ → report with undo instructions
  (backupctl restore + appctl rebuild --all) and "restart serve → first-run"
  note; --skip-backup honored; missing-DB degrades to wipe-only with a note.
  tools/backupctl.py: create (--scope all|db|apps|app, --code-only,
  --with-logs), list (kind/scope/size/purge table), restore (--force),
  factory-reset (typed-RESET prompt, EOF-safe non-tty message, --force for
  scripts) — deliberately does NOT use the shared bootstrap so restore into
  an empty data dir never auto-creates a DB first (tested). Maintenance
  integration: run_all + cleanup-retention now prune expired factory backups
  (manual backups never pruned), report/summary extended.
- **Validation:** `uv run pytest` → 362 passed; ruff clean.
- **Known issues:** none.
- **Next:** Q4 UI (Backups & reset admin page, gear export/rebuild).

## 2026-07-05 — G02 Q4 UI — complete

- **Summary:** Admin "Backups & reset" page: create-backup form (scope radio,
  app selector, code-only + with-logs toggles) with download of the produced
  zip; existing-backups list (kind/scope/size/created/purge) with download +
  confirmed delete; import-app section (zip upload staged to tmp, owner
  selector, optional rename, deploy toggle, standard deploy-outcome panel);
  danger zone factory reset gated by typed RESET with BOTH a disabled button
  and a server-side re-check (AppTest can click "disabled" buttons — the gate
  is real), report + "restart serve" notes rendered BEFORE any DB access on
  subsequent reruns (a rerun after reset must not recreate an empty DB file —
  caught by test, fixed by reordering render()). Gear dialog: rebuild warning
  + "Rebuild now" when the venv is missing, and an Export expander
  (code-only toggle, archive also kept in backups/manual/, download button);
  dashboard cards show "⚠ rebuild required". common.require_user now degrades
  gracefully when the schema is missing (post-reset in-process rerun).
- **Validation:** `uv run pytest` → 367 passed; ruff clean.
- **Known issues:** import-via-UI upload widget not drivable by AppTest
  (service + CLI covered); manual checklist gets it.
- **Next:** Q5 docs & final verification.

## 2026-07-05 — G02 Q5 Docs & final verification — complete — GOAL DONE

- **Summary:** docs/backups-and-restore.md written: where archives live and
  why backups/ survives wipes, all four scopes with a restorable-how table,
  restore + rebuild semantics, export/import incl. un-delete and the
  argon2-portability note, factory reset (order of operations, undo recipe,
  CLI/UI confirmation contract), the machine-migration runbook, and the
  retention summary table. Cross-updates: README (backupctl + new appctl
  subcommands, docs index), troubleshooting ("rebuild required" symptom +
  disaster-recovery pointer), manual smoke checklist (backups/download,
  export→import round trip, factory reset against a scratch data dir).
- **Final validation (this Mac):** unit 367 · `-m integration` 2 · `-m e2e` 4
  (G01 deployment round trip + G02 backup→wipe→restore→rebuild→serving,
  export→delete→import→serving, soft-delete un-delete) · `-m caddy` 3 ·
  `uv run ruff check .` clean · doctor all checks passed.
- **Known issues:** none. PROGRESS.md carries the DoD closure and accepted
  limitations. Tagged v0.2.0.
- **Next:** none — G02 complete.

## 2026-07-06 — App-creation flow: LLM authoring kit — complete

- **Context:** Follow-up enhancement after G02 (not a new goal phase). Replaces
  the "prime the LLM at the end" flow with an authoring kit that makes the
  coding LLM WALoader-native from the first message.
- **Summary:** New top-level `authoring_kit/` folder (plain repo files — NOT
  downloadable in the UI; the operator feeds them to their LLM per the user's
  instruction). Contents: README.md (operator wiring notes — the only file not
  meant for the LLM), SYSTEM_PROMPT.md (paste into the assistant's system
  slot: every app is a WALoader Streamlit app, styled per DESIGN_LANGUAGE.md,
  Dataset-Concept-backed, test-included, single-bundle output, hand-hold
  non-technical users), 01-building-waloader-apps.md (engineering contract:
  project shape, the data.py/sample_data.py preview-parity pattern with the
  waloader_sdk try/except ImportError fallback, Dataset Concepts, mandatory
  pytest, dependency rules, and the FULL single-markdown bundle format incl.
  nested-fence rule + path rules, plus hard don'ts), 02-previews.md (cadence:
  ask once "every change or on request"; the preview ladder — runnable local
  streamlit > styled mockup > text wireframe — all driven from the real code +
  sample data so previews match the deploy), 03-help-and-faq.md (capabilities,
  CANNOT list, limits table, click-by-click tutorial, Q&A for the canonical
  questions, example prompt templates, tone). DESIGN_LANGUAGE.md is referenced
  everywhere but NOT shipped (user provides it).
- **Doc-sync:** tests/test_authoring_kit.py pins kit facts to real defaults
  (UploadsConfig limits, dataset formats, bundle markers, nested-fence + path
  rules, SDK/parity pattern, mandatory tests, DESIGN_LANGUAGE references,
  canonical FAQ questions) — whitespace-normalized so prose wrapping can't
  break assertions. Changing platform limits or the bundle contract now fails
  CI until the kit is updated.
- **Also:** docs/llm-bundle-prompt.md rewritten as a "superseded — see
  authoring_kit/" fallback for un-primed LLMs; README docs table + user-flow
  updated to point at the kit.
- **Validation:** `uv run pytest` → 380 passed (+13); `uv run ruff check .`
  → clean.
- **Known issues:** none. DESIGN_LANGUAGE.md must be added to authoring_kit/
  by the operator before use (the kit instructs the LLM to ask for it if
  absent).

## 2026-07-06 — Field-feedback round 1 (first real RHEL + Sonnet 4.6 usage)

- **Bundle tolerance (parser):** sanitize_bundle_text strips trailing
  `<workspaces-note>…</workspaces-note>` lines (corporate LLM export
  artifact) and unwraps a single accidental outer ```markdown fence (the
  copy-from-chat mistake that failed the user's first real upload) — only
  when the opener has no real info string, so clean bundles are untouched;
  notes inside file contents are preserved (tested).
- **Bundle-declared Dataset Concepts:** new optional metadata key
  `dataset_concepts = [...]`; deployment auto-creates missing concepts
  (invalid names warn, never fail). Sample bundle + kit + contract doc
  updated; e2e asserts auto-creation.
- **SDK behavior change:** load_dataset now returns None for an UNDEFINED
  concept (previously raised UnknownConceptError → ugly traceback screen in
  the field). required=True keeps the distinct hard errors. Rationale: a
  non-technical owner fixes "not defined" and "not uploaded" identically on
  the Datasets page, so both must render the friendly empty state.
- **UI fixes from field testing:** (1) stale deploy-failure panel suppressed
  once the app has a clean successfully-deployed version (signal:
  last_deploy_error cleared + current_version set); (2) successful
  create/retry now lands on the Dashboard (st.switch_page via new ui/nav
  registry) instead of a confusing stayed-on-Create screen; (3) flash-toast
  helper (queued across st.rerun, rendered by app.py) gives success feedback
  for dataset uploads/replacements/concept save/delete, app-user
  create/delete/deactivate/reactivate/attach, lifecycle stop/resume/restart/
  delete, and the user-mgmt toggle; (4) gear dialog gained real "Manage
  Dataset Concepts" / "Manage app users" buttons that switch page with the
  app preselected; (5) gear icon vertically centered on cards
  (vertical_alignment="center"); (6) clipboard hint caption next to error
  blocks (browsers block the copy button over plain HTTP — documented, not
  fixable app-side).
- **serve --daemon / --status / --stop:** detached Streamlit (own session,
  no controlling terminal → survives SSH logout), pidfile with create_time,
  logs to data/logs/waloader/serve.log; --status/--stop use a light config
  path that never migrates/creates the DB. Docs: process-management daemon
  section incl. logind KillUserProcesses / loginctl enable-linger note;
  troubleshooting runbook now says --daemon; new entries for the copy button
  and dying-on-logout.
- **Kit hardening for Sonnet-class models (answers the pending question —
  the kit worked in the field; both real failures were bundle artifacts, now
  tolerated platform-side AND taught kit-side):** never wrap the bundle in
  an outer fence (SYSTEM_PROMPT + 01), declare dataset_concepts, ALWAYS
  include the require_login gate (platform toggle alone controls login —
  field confusion), concept-named empty-state message.
- **Validation:** unit 399 passed (+19); e2e 4 passed (real deployments of
  the updated sample bundle: auto-created concept + auth gate live); caddy 3
  passed; ruff clean.
- **Known issues:** none.

## 2026-07-06 — Operator tool: safe remote update (deploy push)

- **Context:** Follow-up after the RHEL rollout. The box has no git; the user
  was doing git-pull-on-Windows → zip → scp → unzip, which is unsafe (can
  clobber config, never removes deleted files, forgets uv sync/restart).
  Requested a one-command safe updater; chose "push over SSH, fully
  automatic".
- **Summary:** New src/waloader/tools/deploy.py (package / apply / push),
  stdlib-only and dependency-free (shells out to system ssh/scp, so existing
  password/key auth just works — deliberately NOT Fabric/paramiko). Safety
  model: payload = git-tracked files only (git ls-files), so git-ignored
  data/ + config/waloader.toml + .venv are inherently excluded; a hardcoded
  PROTECTED denylist refuses to overwrite OR delete them even if force-added
  to git; files removed between versions are cleaned via a per-deploy
  .deploy/manifest.json diff (never protected); apply refuses a non-install
  target (no pyproject.toml) and aborts on missing sentinels (empty/garbage
  payload guard); zip-slip guard on extract; --dry-run on push and apply.
  push: package here → scp tarball + the apply script → remote
  `python3 deploy.py apply` (login shell) → uv sync → db migrate → restart
  the serve daemon (child apps keep running). The apply path is Python
  3.6-compatible so stock RHEL python3 runs it standalone (documented ruff
  per-file-ignore for the pyupgrade rules that would force 3.7+ idioms).
  config/deploy.example.toml (+ config/deploy.toml git-ignored) holds the
  connection; all values also available as CLI flags.
- **Tests:** tests/test_deploy.py — protection/denylist, safe_target
  traversal, git payload excludes runtime state (incl. the force-added case),
  no-git walk, manifest-diff deletions never include protected, full
  package→apply round trip proving runtime state preserved across two updates
  + stale-file cleanup, non-install-target refusal, missing-sentinel abort,
  dry-run no-op, push --dry-run needs no ssh + required-field validation.
  Real smoke: packaging THIS repo yields 148 files, MANIFEST present, ships
  the example config, and leaks zero data//config/.venv files.
- **Docs:** docs/deploying-updates.md (why-not-unzip, setup, push, manual
  package+apply, safety guarantees); README docs table + CLI list;
  troubleshooting RHEL runbook cross-link.
- **Validation:** unit 412 passed (+13); ruff clean; deploy.py parses and has
  no walrus/match/capture_output/text= (3.6-safe).

## 2026-07-06 — deploy tool: Windows fixes + genericized example

- **Bug (field, Windows):** `deploy push` crashed with a raw
  FileNotFoundError [WinError 2] at the first `subprocess.run(["ssh", ...])` —
  ssh not on the PATH the Python process saw (user reaches the box via Git
  Bash/PuTTY, not Windows OpenSSH). Now: `_require_binary` preflights ssh/scp
  (shutil.which or absolute-path existence) with an actionable error (install
  OpenSSH, or set ssh/scp to Git-for-Windows paths, or use manual
  package+apply); all ssh/scp spawns wrapped to convert OSError → DeployError.
- **Second Windows bug found in review (would hit next):** scp reads a local
  `C:\...\payload.tar.gz` as host `C`. Fixed by copying deploy.py next to the
  tarball and running scp with cwd=staging and BARE filenames (no drive
  letter/backslashes). Regression-tested.
- **Config keys added:** `ssh` / `scp` binary overrides (deploy.toml + --ssh/
  --scp flags); identity_file now expanduser'd. Remote command construction
  simplified to a single `bash -lc <quoted>` arg (one level of quoting) for
  robustness.
- **Genericized config/deploy.example.toml** — it had contained the user's
  real internal hostname/user/path (inferred from earlier screenshots and
  baked in as if placeholders); replaced with obvious placeholders so no
  internal detail sits in git. (Answered the user's "how did you know the
  host?" — that was the source.)
- **Docs:** deploying-updates.md gained a Windows ssh-not-found section and a
  "run via serve --daemon, not foreground" restart caveat (a foreground serve
  holding the port makes the managed daemon restart collide).
- **Tests:** +4 (missing-ssh actionable error, absolute-path must-exist,
  scp-uses-bare-filenames regression, custom ssh/scp binaries respected).
- **Validation:** unit 416 passed (+4); ruff clean; self-package still ships
  deploy.py + example config with zero data//config leaks.

## 2026-07-06 — deploy: corporate-index uv env + venv-python restart

- **Bug (field, corporate RHEL):** push connected but apply's `uv sync` /
  `uv run` hit pypi.org and DNS-failed ("Name or service not known",
  "Failed to fetch hatchling") — the box is behind a private index and apply
  ran uv WITHOUT the UV_CONFIG_FILE/UV_SYSTEM_CERTS env WALoader uses for
  child-app installs. Also `uv run ... serve --stop` triggered a full project
  rebuild before doing anything, compounding the failure.
- **Fix:** apply now (1) auto-reads the box's own config/waloader.toml [uv]
  section (minimal 3.6-safe parser; reads waloader.toml only, never the uv
  config file's contents) and exports UV_CONFIG_FILE / UV_SYSTEM_CERTS /
  SSL_CERT_FILE / SSL_CERT_DIR for `uv sync` — so if child-app deploys work on
  the box, updates work with zero extra config; (2) runs stop/migrate/daemon
  via the venv python directly (<root>/.venv/bin/python -m waloader.tools.*)
  instead of `uv run`, so they never rebuild; (3) `_run` catches OSError so a
  missing uv is a clean failed step, not a traceback. New `[remote.env]`
  (deploy.toml) + `--env KEY=VALUE` (apply/push) override/extend the derived
  env; explicit wins over auto-read.
- **Example/docs:** deploy.example.toml gained [remote.env] guidance + Cmder
  ssh/scp path example; deploying-updates.md gained "corporate/offline server"
  and Cmder sections and the "set uv to an absolute path" note. Answers the
  user's question: usually you need NOT re-specify Linux paths (auto-read),
  but set `uv` absolute path and optionally [remote.env] if needed.
- **Tests:** +3 (auto-read [uv] parsing incl. inline-comment stripping;
  ignores other sections/missing; uv sync gets derived env with --env
  override winning; push forwards [remote.env] into the remote apply command).
- **Validation:** unit 419 passed (+3); ruff clean.

## 2026-07-06 — deploy: uv binary also auto-read from waloader.toml (consistency)

- **Feedback:** user flagged an inconsistency — the uv ENV vars (UV_CONFIG_FILE
  etc.) auto-read from the box's waloader.toml with deploy.toml override, but
  the uv BINARY path defaulted to "uv" and had to be repeated in deploy.toml.
  They wanted the same rule for both: empty in deploy.toml by default (read
  from waloader.toml), deploy.toml wins if set.
- **Change:** refactored the minimal waloader.toml reader into
  _read_waloader_section; uv binary now resolves as deploy.toml `uv`
  (if set) → waloader.toml [executables].uv_binary → "uv". apply_package uv
  default is now "" (auto); push omits --uv when unset so the server resolves;
  CLI --uv default "". The [uv] env auto-read/override already worked and is
  unchanged. Clarified deploy.example.toml (LOCAL ssh/scp vs SERVER
  remote_dir/uv/remote_python/[remote.env]; all server values default to the
  box's waloader.toml, deploy.toml overrides) and the docs.
- **Answers to the user:** the `uv = "/home/.../uv"` example is the LINUX/box
  path; no clash — deploy.toml is empty by default and reads from
  waloader.toml, and if you DO set a value it takes precedence (now true for
  the uv binary too, not just the env).
- **Tests:** +2 (auto-read uv_binary from [executables]; precedence: unset →
  waloader.toml value, set → override wins).
- **Validation:** unit 421 passed; ruff clean.

## 2026-07-07 — Field usability round 2: gear dialog, user-mgmt visibility, centering

- **Root-caused live in the preview browser** (not just from code): reproduced
  each bug before fixing.
- **Restart/Stop/Resume/Delete confirmation closed the modal:** the confirm-
  setting buttons called plain `st.rerun()`, which CLOSES an st.dialog
  (documented Streamlit behavior; confirmed on 1.58). Fix: fragment-scoped
  rerun (`st.rerun(scope="fragment")`) re-renders the dialog in place. Extracted
  `_request_confirm`/`_dismiss_confirm` helpers so the fragment-scope is
  directly unit-tested (a plain rerun would fail the test). Export "Create
  archive" had the same latent bug (closed before showing the download) — also
  fragment-scoped now. Verified live: clicking Restart now keeps the dialog
  open and shows "Really restart '…'?" with Yes/Cancel; Cancel returns to the
  actions; both without closing.
- **Enabling Users Management did nothing:** the SDK is correct — but the app's
  code must call `require_login()`, and pre-kit apps don't, so WALoader (which
  can't inject login into child code) silently ignored the toggle. Added
  `app_users_service.code_enforces_login(config, app)` (scans the current
  version source for require_login / waloader_sdk.auth) and surfaced a clear
  warning on the card ("⚠ login ON but not enforced by the app code") and in
  the gear dialog (what it means + how to fix: regenerate with the kit →
  Update code). Verified live.
- **Gear not centered:** with a long, wrapping app name and center alignment
  the gear floated in the gap between the two lines. Changed the header row to
  `vertical_alignment="top"` (and [5,1]) so the gear sits next to the first
  line. Verified live.
- **Verification aid:** sidebar now shows `platform vX.Y.Z` so an update is
  visible in the UI, not just the CLI.
- **Tests (the ones that should have caught these):** code_enforces_login
  (4 cases); dashboard AppTest — confirmation renders in the open dialog vs.
  plain action buttons, login-not-enforced warning shown/hidden across the
  three states, confirm-helper fragment-scope guards, gear top-alignment.
  Learned AppTest can't model a dialog persisting across a fragment rerun, so
  the flow is tested in two halves + the helper-level scope guard.
- **Validation:** unit 433 passed (+14); e2e 4 passed; ruff clean; all three
  fixes confirmed in a real browser via the preview harness.

## 2026-07-07 — Field round 3: build badge, gear H-centering, human-flow test mandate

- **Gear horizontal centering (the real complaint):** last round I fixed
  vertical centering but the glyph was left-hugging its button. Added
  use_container_width=True so the button fills its column and its centered
  label centers horizontally. Verified live: glyph center-x == button
  center-x (204 == 204).
- **Per-deploy build badge:** subtle gray, fixed bottom-right corner, shows
  `vX.Y.Z · <git-sha> · <date>` so every deploy is visibly distinguishable
  (the SHA/date change per push even when the semantic version doesn't). The
  deploy tool now bakes git_sha + created_at into .deploy/manifest.json at
  package time (the box has no git); common.build_info() reads that on a
  deployed box, or falls back to live git in dev ("<sha>-dev"), or version
  only. Replaced the sidebar version caption. Verified live: badge reads
  "v0.1.0 · 06119cf-dev", color gray, fixed bottom:6/right:12.
- **require_login mandate:** confirmed the kit already requires calling
  require_login() in EVERY app (§5) and the sample bundle has it, so
  kit-generated apps always honor the toggle; the "login not enforced"
  warning remains only as a detector for pre-kit apps (regenerate → Update
  code fixes them). No kit change needed.
- **AGENTS.md — human-flow tests are now mandatory:** added a rule that
  service-layer tests are necessary but insufficient (they were green while a
  dialog closed on click and a toggle did nothing); every user-facing flow
  must have a test that reproduces the human's clicks and asserts what a
  human expects at each step (AppTest), with a seam-guard + one real-browser
  check when AppTest can't model an interaction; field bugs get a failing
  human-flow test first, then the fix.
- **Tests (+7):** build_manifest carries git_sha; _git_sha empty without git;
  build_info reads manifest / live-git fallback / version-only; badge markup
  emitted; entrypoint calls the badge. Deploy fake-run stubs updated for the
  new git probe.
- **Validation:** unit 440 passed; ruff clean; gear centering + badge
  confirmed in a real browser via the preview harness.
