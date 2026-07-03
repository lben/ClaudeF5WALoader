# Goal G01 — WALoader: complete, feature-complete build

**Status:** active (the only goal). This file is the product specification of record.
Read `AGENTS.md` first for the operating rules; this file assumes them.

## 1. Goal statement

Build WALoader end to end: a portable (macOS/Windows dev, Red Hat Linux prod)
Streamlit platform that lets authenticated finance users upload LLM-generated
Streamlit projects as structured markdown bundles, then validates, versions, deploys,
proxies, monitors, and manages those apps — including Dataset Concepts, a reusable
user-management module with SDK, notifications, backups/retention, an admin panel,
CLI tooling, tests, and documentation.

The goal is complete only when every item in §9 Definition of Done passes. "Bug free"
is operationalized as: the full test suite (unit + integration + e2e) green on the
current machine, `ruff check` clean, the `doctor` command passing, and the manual
smoke checklist verified against a running instance.

### Resume protocol

Work happens across multiple sessions and machines. At every phase boundary update
`PROGRESS.md`, append to `DEVLOG.md`, and make a git commit. A fresh session resumes
by reading `PROGRESS.md` and continuing from the first unchecked item — never by
restarting completed phases.

## 2. Out of scope (do not build)

- Sandboxing/isolating child app code. Trust model (must be stated in docs): child
  apps are trusted code uploaded by authenticated internal users and run with the same
  OS user as WALoader. WALoader validates bundle *paths*, not code behavior.
- Per-app git repositories (WALoader-native versioning instead), FastAPI, Celery,
  Redis, Docker/Kubernetes, per-app OS users, mandatory systemd/cron, SSO/LDAP,
  browser-automation UI tests, HTTPS termination (internal HTTP only).
- Persistent browser login cookies. Streamlit has no official cookie API; login state
  lives in `st.session_state` and is lost on a full browser refresh. Accepted for MVP;
  document it.
- Binary files inside markdown bundles. Bundles are text-only; tabular data reaches
  apps via Dataset Concepts. Document as a known limitation.

## 3. Architecture decisions (binding)

1. **Layout:** `src/waloader/` (platform: config, db, migrations, models,
   repositories, services, notifications, ui, tools), `src/waloader_sdk/` (child-app
   SDK, dependency-light), `tests/`, `docs/`, `config/`, `examples/`, `goals/`.
   Runtime state lives under `data/` (git-ignored).
2. **Processes:** one WALoader Streamlit process (UI + a background maintenance thread
   started as a process-wide singleton via `st.cache_resource`); child apps are
   detached OS processes that survive WALoader restarts; CLI tools are separate
   processes calling the same service layer. Health checks and scheduled maintenance
   run only while WALoader (or a CLI command) runs — document this limitation.
3. **SQLite:** WAL journal mode, `busy_timeout >= 5000ms`, foreign keys ON, short
   transactions. Multiple processes (UI, CLIs, child apps via SDK) share the DB.
   Migrations are numbered SQL files under `src/waloader/migrations/`, tracked in a
   `schema_migrations` table, runnable via CLI and (if configured) auto-run at startup.
   Never mutate schema ad hoc.
4. **Config precedence:** built-in defaults → TOML file → environment variable
   `WALOADER_CONFIG` (path to TOML) → **DB-stored settings** (admin-panel edits).
   Bootstrap settings (`paths.data_dir`, DB location derivation) are TOML/env only,
   since the DB isn't open yet. The admin panel shows each setting's effective value
   and its source, and writes changes to the DB `settings` table. Local per-machine
   file: `config/waloader.toml` (git-ignored); committed template:
   `config/waloader.example.toml`.
5. **Canonical dataset format:** every dataset upload is converted to a canonical
   **Parquet** file at upload time (original file preserved alongside). Schema
   inference/diff run at upload time against the declared Excel sheet when relevant.
   The SDK reads only canonical Parquet — so child apps need pandas+pyarrow but never
   openpyxl/xlrd.
6. **Child-app environment injection** (set at launch by the process manager):
   `WALOADER_APP_SLUG`, `WALOADER_APP_NAME`, `WALOADER_DB_PATH`,
   `WALOADER_DATA_DIR`, `WALOADER_DATASETS_DIR` (the app's datasets dir), and
   `PYTHONPATH` with WALoader's `src` **prepended** (never overwritten):

   ```python
   existing = env.get("PYTHONPATH", "")
   env["PYTHONPATH"] = sdk_path + os.pathsep + existing if existing else sdk_path
   ```
7. **Update semantics:** deploying a new version builds, tests, and installs it
   *before* touching the running old version; only on success is the old process
   stopped and the new one started (same port). A failed update leaves the previous
   version running and surfaces the error. Create/retry-create/update/retry-update all
   reuse this one deployment pipeline.
8. **Time:** UTC ISO 8601 everywhere in DB and logs.

## 4. Product specification

### 4.1 Roles

- **WALoader admin** — operates the platform; admin panel, platform config, all apps,
  WALoader user management, reconciliation, Caddy control.
- **App owner** — uploads/manages own apps; manages app users when user management is
  enabled for their app.
- **App user** — logs into and uses a deployed child app (when that app has user
  management enabled).
- **Anonymous visitor** — sees only the login screen.

### 4.2 Create-app user flow

1. User logs into WALoader; dashboard lists the apps they own; a "Create new app"
   button is present.
2. Create screen: markdown bundle upload, app name, description, "Users Management
   Support" toggle, and access to Dataset Concepts definition.
3. As the app name changes, WALoader derives the slug, checks name+slug availability
   against the DB, and shows a ✓ (available) or ✗ (taken) with a smaller italic
   message under the field when taken. (Streamlit reruns on input change; on-change
   checking is acceptable — no keystroke-level requirement.)
4. Submit → backend pipeline (§4.7). Success → success screen/modal with message, a
   **copyable code block containing the app URL**, and a link to open the app (new tab
   where Streamlit allows). Failure → error screen/modal with a concise message, a
   **copyable code block** concatenating deployment/test/dependency/runtime errors,
   and an upload control to submit a fixed bundle and retry.

### 4.3 Dashboard and app management

- Login, logout, password change for WALoader users.
- Vertically scrollable grid of app cards (one per app) with clear status indicators.
- Each card has a gear icon opening a configuration modal (`st.dialog`) with:
  update code (upload new bundle, same success/error/retry flow), stop, resume,
  restart, delete — each with a confirmation dialog — plus enable/disable Users
  Management Support and open Dataset Concepts mapping.
- Delete is soft: stop the app, archive source/config/metadata/log references into a
  compressed archive under `data/archives/`, mark `deleted` (pending hard delete),
  hide from the dashboard, release nothing else. Hard delete (files + port release)
  happens after the configured retention (default 183 days) via maintenance.

### 4.4 Markdown bundle contract (exact)

A bundle is one UTF-8 markdown file.

**Metadata block** — the first fenced code block in the document, info string
`toml waloader-bundle`, body is TOML:

```toml
bundle_format = 1        # required; only 1 is accepted
entrypoint = "app.py"    # required; must match a declared file path
app_name = ""            # optional, informational (the UI field wins)
description = ""         # optional
```

Unknown keys → warning, not error. Content before the metadata block is ignored.

**File blocks** — each file is declared by a level-2 heading `## file: <relative/posix/path>`
(`file:` case-insensitive, flexible whitespace). The first fenced code block after the
heading is the file's content: opening fence of ≥3 backticks plus optional language
tag; per CommonMark, the block closes at a line whose leading backtick run is ≥ the
opening run. Content between fences is preserved verbatim; files are written UTF-8
with a trailing newline. Prose between file sections is ignored (LLMs may annotate).
Empty content blocks are allowed.

**Nested fences:** file contents will often contain ``` (Streamlit apps embed
markdown). Authors must use a longer outer fence (e.g. 4+ backticks); the parser must
handle this per the CommonMark rule above, and the LLM authoring template
(`docs/llm-bundle-prompt.md`) must instruct it.

**Validation — reject with clear, copyable errors:**

- absolute paths, Windows drive letters, backslash separators, `..` anywhere;
- any path equal to or under `private/`;
- `.git/` and hidden files/dirs, except an allowlist (`.streamlit/`, `.gitignore`);
- duplicate paths; zero file blocks; entrypoint not among declared files;
- a `## file:` heading with no following fenced block;
- more than `uploads.max_bundle_files` (default 200) files;
- bundle larger than `uploads.max_markdown_bundle_mb`; not valid UTF-8;
- missing/invalid metadata block or unsupported `bundle_format`.

Reconstruction writes only inside the target version's `source/` directory — it can
never overwrite WALoader files. Every upload creates a new app version.

### 4.5 Slugs

Derivation: Unicode-normalize to ASCII, lowercase; every run of non-alphanumerics
becomes one hyphen; strip leading/trailing hyphens; max 60 chars (trim, then strip
trailing hyphen); must be non-empty, unique among all non-hard-deleted apps
(case-insensitive; soft-deleted apps keep their slug reserved until purged), and not
a reserved name.

```text
"Client Positions Dashboard" -> "client-positions-dashboard"
"P&L Explain 2026!"          -> "p-l-explain-2026"
```

Reserved names: `waloader admin login logout api static assets private health apps
app-link caddy`.

Used in URLs (`http://servername:8080/apps/<slug>`), folders (`data/apps/<slug>/`),
logs (`data/logs/apps/<slug>/`), and DB (`apps.slug`). App names are also unique
case-insensitively.

### 4.6 Versioning and storage layout

```text
data/apps/<app_slug>/
  versions/
    000001/
      source/              # reconstructed project
      manifest.json        # files, sizes, hashes, entrypoint, created_at, created_by
      uploaded_bundle.md   # original upload preserved byte-exact
    000002/ ...
  runtime/                 # venv, pid file, etc.
  datasets/                # per-concept originals + canonical parquet
```

The DB tracks per app: id, owner, name, slug, description, current version, all
versions, deployment status, runtime state, created_by/at, updated_at, allocated
port, Caddy route (if enabled), and deployment/log references (see Appendix A).

### 4.7 Deployment pipeline (one composable service)

Steps, each producing structured success/error results and logs:

1. Parse + validate bundle (§4.4) → create new version folder + manifest.
2. Dependency policy check (§4.8) on `pyproject.toml` `[project.dependencies]` if
   present.
3. Create/refresh the per-app venv (`uv venv --python <configured>`), install
   dependencies via uv (§4.9): the declared dependency list (never the app itself as
   a package), or the approved base set (the platform stack's data libs: streamlit,
   pandas, plotly, duckdb, pyarrow) when no pyproject exists. Ensure streamlit is
   present.
4. If the version contains tests (`tests/` or `test_*.py`), install pytest into the
   app venv and run them with a configurable timeout (default 300s); failure aborts
   deployment with copyable output.
5. Allocate a port (§4.11) — reuse the app's existing port when possible.
6. If updating: stop the old version's process now (only after 1–4 succeeded).
7. Launch the child detached: `<venv python> -m streamlit run <entrypoint>
   --server.port <port> --server.headless true --browser.gatherUsageStats false`
   plus `--server.baseUrlPath apps/<slug>` when Caddy is enabled; bind address
   configurable (default `127.0.0.1` behind Caddy, `0.0.0.0` direct mode); env per §3.6;
   stdout/stderr → the version's `runtime.log`.
8. Update Caddy config + reload if enabled; on Caddy failure, fall back to the direct
   port URL and report the Caddy error clearly (deployment itself still succeeds).
9. Initial health check (§4.13) with a startup grace window; record result; final
   state `running` or `deployment_failed`.

The same pipeline serves create, retry-create, update, retry-update (§3.7). Every
attempt is recorded in a `deployments` table with timing, status, and error summary.

### 4.8 Dependency policy

```toml
[dependencies_policy]
allow_app_dependencies = true                     # false -> only the approved base set
allow_direct_url_dependencies = false             # "pkg @ https://..." forms
allow_vcs_dependencies = false                    # git+... forms
allow_path_dependencies = false                   # local path / file:// forms
require_admin_approval_for_new_dependencies = false  # true -> block until admin approves
```

Parse requirement strings with `packaging.requirements`; reject violations with clear
user-facing errors naming the offending requirement and the policy that blocks it.
When approval is required, the deployment stops in a clearly reported state an admin
can approve from the admin panel (approval stored in DB, then retry proceeds).

### 4.9 uv invocation contract

A single command/environment builder used by preflight and deployments:

- pass `UV_CONFIG_FILE` if `uv.config_file` is set; `UV_CACHE_DIR` if `uv.cache_dir`
  is set; `UV_SYSTEM_CERTS=true` if `uv.system_certs`; `SSL_CERT_FILE`/`SSL_CERT_DIR`
  if configured; insecure-host settings only when explicitly configured;
- use `--python <path>` when `executables.python_binary` is set;
- preserve existing environment variables unless intentionally overridden;
- **never read/print/log the contents of `uv.config_file`;** redact credentials
  (`scheme://user:pass@`, `?token=...`) from any surfaced command line, error text, or
  log line — implement one redaction function and use it everywhere.

Preflight service: run `uv pip install --dry-run <pkgs>` (default `["pandas"]`,
configurable `uv.preflight_packages`) with the same binary/env deployments use;
failures produce a copyable error block.

```toml
[executables]
python_binary = ""   # default: shutil.which("python3.12") or current interpreter
uv_binary = "uv"
caddy_binary = ""    # default: shutil.which("caddy"); required only if caddy.enabled

[uv]
config_file = ""     # path only; contents are secret; optional (unset at home)
cache_dir = ""       # default derived: data/uv-cache
system_certs = false # true on corporate machines
ssl_cert_file = ""
ssl_cert_dir = ""
allow_insecure_hosts = []
preflight_packages = ["pandas"]
```

### 4.10 Process management

Pure Python, `subprocess` + `psutil`. Operations: start, stop, restart, status,
health, logs, reconcile — exposed identically through services, `appctl` CLI, and the
admin UI.

- Detached spawn per AGENTS.md so children survive WALoader restarts.
- Record `pid` **and** `psutil` process `create_time` to defeat PID reuse; a process
  "is ours" only if both match.
- Stop = terminate the process tree (children included) gracefully, then kill after a
  timeout (default 10s).
- Startup reconciliation (also admin-triggered): compare DB state to live processes
  and ports; detect stale PIDs, dead-but-`running` apps, occupied/missing ports; fix
  DB state; offer admin-only "resume selected/all previously running apps".

### 4.11 Port allocation

```toml
[ports]
waloader_port = 8501
child_app_start = 8601
child_app_end = 8999
caddy_public_port = 8080
```

Allocation checks DB reservations **and** actual socket availability, reserves
atomically (single SQLite transaction), keeps an app on its port across restarts when
possible, releases ports on hard delete, and reports exhaustion clearly.

### 4.12 Caddy reverse proxy (optional but first-class)

No root, no global install, no port 80 assumptions; binary path configurable; default
public port 8080. Clean URLs `http://host:8080/waloader` and
`http://host:8080/apps/<slug>`; fallback direct URLs `http://host:<port>`.

- Caddyfile is **generated from the DB** into `data/caddy/Caddyfile` (users never
  hand-edit); path-preserving `reverse_proxy` routes (WebSockets work natively in
  Caddy v2); Caddy admin endpoint on a configurable localhost port; access/error logs
  under `data/logs/caddy/`.
- Service + `caddyctl` CLI + admin panel: generate, validate (`caddy validate`),
  start (managed child process via `caddy run`), stop, reload (`caddy reload`),
  status, view generated Caddyfile, view logs.
- When enabled, WALoader itself is served under `/waloader` (the `serve` launcher
  passes `--server.baseUrlPath=waloader`) and children under `/apps/<slug>`.
- If Caddy fails or is disabled, everything works via direct ports and the UI shows
  direct URLs plus the Caddy error where relevant.

### 4.13 Health checks and runtime state machine

States: `created, deploying, deployment_failed, running, stopped, failed, deleted,
pending_delete` — with an explicit allowed-transition map enforced in one place.

A health check records: process exists, PID+create_time alive, port open, HTTP probe
(`GET /<base>/_stcore/health`, fall back to TCP connect), last healthy time, last
failed time, failure reason. Background loop interval configurable (default 30s);
manual check via CLI/admin. Dashboard cards show status clearly.

### 4.14 Notifications (crash emails)

Never notify for: dependency install failure, test failure, initial deployment
failure, user-triggered stop/restart, app update failure. Notify only when **all**
hold: app was `running`, passed initial health checks, survived the production grace
period (default 180s, configurable), a later check detects death/repeated unhealthy,
state transitions `running → failed`, and no email was already sent for this failure
event (dedupe key stored in DB; reset when the app returns to `running`).

All email flows through one seam — a single `send_mail` function in its own small
module (e.g. `src/waloader/notifications/mailer.py`) with exactly the corporate
signature:

```python
send_mail(subject=..., sender='[sender_email]', recipients=[...], html_body=...)
```

The shipped implementation is a **stub that logs** the subject and recipients (never
secrets) and returns normally; the DB dedupe record is written as if sent. At work
the operator replaces this one function's body with the real corporate mailer —
nothing else changes. No SMTP machinery, no adapter registry. The module's docstring
must say exactly this ("replace this function body with the corporate mailer"). The
notification *service* (when to send, dedupe, cooldown, recipients, HTML body
construction) is fully implemented and tested against the stub via monkeypatching.

Email body: simple Outlook-compatible HTML (tables/inline styles), containing app
name/slug, failure reason, timestamps, and log paths — never secrets or log dumps.
Recipient: the app owner's email (admin CC configurable).

### 4.15 Dataset Concepts

Apps declare named concepts (e.g. `clients`, `transactions`, `positions`); finance
users reference them in LLM prompts as `[clients]` etc. Per app, the mapping screen
supports: add concept (name field + save), list saved concepts, per-concept upload
and delete (delete with confirmation), replacement upload with schema diff.

- Formats: `.csv` (default), `.xlsx`, `.xls`, `.parquet`; allowed extensions and max
  size configurable (`max_dataset_file_mb` default 250).
- **Excel:** when the chosen file is `.xlsx`/`.xls`, show a required sheet-name input
  pre-filled with `Sheet1`; store the sheet name in dataset metadata; use it for
  reading, canonical conversion, schema inference, and both sides of schema
  comparison. CSV/parquet never ask for a sheet.
- Upload stores the original file + canonical Parquet (§3.5) + inferred schema
  (column names + inferred dtypes) in metadata.
- **Replacement:** diff old vs new schema — added columns, removed columns, dtype
  changes. If discrepancies exist, show them in a **copyable code block** and require
  confirmation before overwriting (a mismatch may be fine when the app code was also
  updated). The diff lives in one reusable, tested service function.
- Concept names: validated like mini-slugs (lowercase, `[a-z0-9_]`, unique per app).

### 4.16 User management module (reusable)

Enabled by default for WALoader itself; per-app toggle for child apps. When enabled
for a child app: app users must log in before use (login form is the default screen
for unauthenticated visitors), with logout and password change available. When
disabled: no app-level login required.

Management UI (WALoader admin for platform users; app owner for their app's users):
table of users; create, update, deactivate, reactivate, delete, change password;
email address; free-text observations; dynamic **attachments** (metadata + files,
e.g. access-justification screenshots) stored under the app's data dir. Passwords
hashed with argon2 — everywhere (platform users and app users). Central service +
SDK, never copied into child apps.

### 4.17 WALoader SDK (`waloader_sdk`)

Importable by children via the injected `PYTHONPATH`; resolves context from
`WALOADER_*` env vars; raises clear, actionable errors when run outside WALoader.
Dependency-light: stdlib + pandas/pyarrow (imported lazily) + streamlit (assumed
present in child apps).

```python
from waloader_sdk.datasets import load_dataset, no_data_placeholder
df = load_dataset("clients")            # DataFrame, or None if nothing uploaded yet
if df is None:
    no_data_placeholder()               # renders italic "No data uploaded yet"; st.stop() optional arg
```

`load_dataset(name, required=False)`: canonical-parquet read; `None` when the concept
exists but has no upload (or raise if `required=True`); a distinct error for an
undefined concept.

```python
from waloader_sdk.auth import require_login
user = require_login()                  # slug from env; app_slug=... override supported
```

`require_login`: no-op returning `None` when the app has user management disabled;
otherwise renders login (then `st.stop()`), returns the logged-in user record, and
provides `logout_button()` and `change_password_form()` helpers.

### 4.18 Configuration system and admin panel

Simple TOML; only settings that cannot be inferred are near-required (python/uv
binaries if not on PATH, caddy binary if enabled, uv config file if a private index
is needed, public host for clean URLs). Everything else derives from
`paths.data_dir` (default `./data`) unless overridden:

```text
database_path      = data/waloader.db
apps_dir           = data/apps
logs_dir           = data/logs
backups_dir        = data/backups
archives_dir       = data/archives
uploads_dir        = data/uploads
tmp_dir            = data/tmp
uv_cache_dir       = data/uv-cache
caddy_config_path  = data/caddy/Caddyfile
caddy_logs_dir     = data/logs/caddy
```

Every setting must be documented in `config/waloader.example.toml` (TOML comments)
**and** `docs/configuration.md` with: name, purpose, default, required/optional,
allowed values and the consequence of each, when to change it, behavior if missing,
behavior if wrong, whether it may contain secrets, whether it is safe to commit.
No undocumented settings.

Admin panel (admin-only) — three areas:

1. **Configuration:** python/uv/caddy binary paths, uv config file path, uv cache
   dir, system certs, Caddy enabled + public host/port, data dir (read-only display
   with derived paths), port range, backup/log/deleted-app retention, upload size
   limits, allowed dataset extensions, dependency policy, health-check timing, crash
   notification settings — each showing effective value + source (§3.4).
2. **Processes:** all app statuses, reconciliation run + results, resume selected /
   resume all previously running apps.
3. **Caddy:** status, generate, validate, start, stop, reload, view generated
   Caddyfile, view Caddy logs.

### 4.19 CLI tools (thin wrappers over the same services)

```bash
uv run python -m waloader.tools.db        migrate | status | backup
uv run python -m waloader.tools.appctl    list | status <slug> | start <slug> | stop <slug> | restart <slug> | logs <slug> | health <slug> | reconcile
uv run python -m waloader.tools.caddyctl  generate | validate | start | stop | reload | status
uv run python -m waloader.tools.maintenance backup-db | cleanup-retention | cleanup-logs | archive-deleted-apps | hard-delete-expired-apps | run-all
uv run python -m waloader.tools.users     create-admin | list | reset-password
uv run python -m waloader.tools.serve     # launches WALoader Streamlit with correct port/baseUrlPath from config
uv run python -m waloader.tools.doctor    # environment self-check (below)
```

`doctor` verifies: config loads; binaries resolve and report versions (python, uv,
caddy-if-enabled); data dir writable; DB reachable + migration status; a port from
the child range bindable; uv preflight passes; caddy validate passes (if enabled);
prints an OS/platform report. Exit code reflects overall pass/fail. This is the
first command run when moving to the Red Hat box.

### 4.20 Backups, retention, archival, maintenance

- **DB backup:** consistent snapshot via the sqlite3 backup API; "daily if changed"
  using a content hash compared to the latest backup; retention default 183 days.
- **Deleted apps:** soft-delete → compressed archive (source, config, metadata, log
  references) under `data/archives/` → hard delete + port release after retention
  (default 183 days).
- **Log retention:** default 183 days; rotating file handlers plus age-based cleanup.
- **Scheduling without cron:** the background maintenance thread (§3.2) triggers
  daily jobs; every job is also operator-triggerable via `maintenance` CLI and the
  admin panel.

### 4.21 Logging

Python `logging` (no stray prints outside CLIs), Rich tracebacks in development:

```toml
[debug]
rich_tracebacks_enabled = true
rich_tracebacks_show_locals = false
```

```text
data/logs/waloader/app.log
data/logs/waloader/error.log
data/logs/caddy/
data/logs/apps/<slug>/<version>/deploy.log
data/logs/apps/<slug>/<version>/runtime.log
data/logs/apps/<slug>/<version>/test.log
```

Never log passwords, tokens, credentialed URLs, uploaded data contents, or uv config
contents. Add debug logging around: bundle parsing, reconstruction, dependency sync,
test runs, process start/stop/restart, Caddy generate/reload, health checks, schema
diff, backup/cleanup.

### 4.22 Upload limits

```toml
[uploads]
max_markdown_bundle_mb = 10
max_dataset_file_mb = 250
max_bundle_files = 200
allowed_dataset_extensions = [".csv", ".xlsx", ".xls", ".parquet"]
default_excel_sheet_name = "Sheet1"
```

## 5. Build order (phases — implement strictly in order)

Each phase ends with: its tests green (`uv run pytest`), `ruff check` clean,
`PROGRESS.md` updated, `DEVLOG.md` appended, git commit.

- **P0 Bootstrap:** `git init` + `.gitignore` (`data/`, `private/`,
  `config/waloader.toml`, venvs, caches, `.DS_Store`); uv project (`pyproject.toml`,
  src layout, pinned stack, dev deps pytest/ruff, `uv.lock` committed); `PROGRESS.md`
  with the full phase checklist; `DEVLOG.md`; a first trivial test proving the
  harness runs.
- **P1 Foundation:** config system (§3.4, §4.18) with pydantic validation + derived
  paths; logging + Rich setup; path utilities incl. `private/` guard; SQLite layer
  (§3.3); migrations framework + `001_initial.sql`; domain models; repositories
  (users, apps, versions, ports, datasets, settings, deployments, audit).
- **P2 Security & users:** argon2 hashing; WALoader user model/service; login/session
  service; admin bootstrap (`users create-admin` CLI + first-run path when zero users
  exist); authorization checks (admin-only, owner-only, app-user); configuration
  documentation framework (example TOML + `docs/configuration.md` covering every
  setting that exists so far — kept current from here on).
- **P3 App core services:** slug service (§4.5); bundle parser + safety validator
  (§4.4); filesystem layout service (§4.6); versioning service (manifests, bundle
  preservation, current-version pointer); dependency policy validator (§4.8); uv
  command/env builder + redaction (§4.9); uv preflight service.
- **P4 Runtime services:** port allocation (§4.11); process manager (§4.10);
  Caddyfile generation (§4.12); Caddy process wrapper; deployment pipeline (§4.7);
  create/retry/update/retry-update orchestration (§3.7).
- **P5 Health & notifications:** health check service; state machine with enforced
  transitions; startup reconciliation; notification service + logging `send_mail`
  stub (§4.14) + crash detection with grace period, dedupe, cooldown; integration
  with the health loop.
- **P6 Dataset Concepts:** concept model/repo/CRUD; upload storage (4 formats,
  Excel sheet handling); canonical Parquet conversion; schema inference; reusable
  schema diff; replacement confirmation workflow; `waloader_sdk.datasets`.
- **P7 User management module:** per-app enable/disable; app-user model/repo/CRUD
  (+ deactivate/reactivate/change password); observations; attachment metadata +
  file storage; app login requirement service; `waloader_sdk.auth`.
- **P8 CLI tools:** `db`, `appctl`, `caddyctl`, `maintenance`, `users`, `serve`,
  `doctor` (§4.19) — all reusing services, all with `--help`.
- **P9 WALoader UI core:** login/logout/password change; dashboard shell + app card
  grid; create-app screen with live availability ✓/✗ + italic taken-message; submit
  success/error flows with copyable blocks and retry upload; gear/config modal with
  all confirmed actions (§4.3); shared deployment flow reused everywhere.
- **P10 Dataset & user-management UI:** concepts mapping screen (add, list, upload,
  delete+confirm, replace, sheet-name input, diff display + confirm); admin UI for
  WALoader users; owner UI for app users; observations + attachments UI.
- **P11 Admin panel:** configuration, processes, and Caddy panels (§4.18).
- **P12 Backups/retention/maintenance:** backup service, archive service, retention
  cleanups, background maintenance thread + operator triggers (§4.20).
- **P13 Documentation:** everything in §7, including the sample bundle and the LLM
  authoring prompt; README with quickstart for macOS/Windows/RHEL.
- **P14 Hardening & final verification:** UI polish (empty states, confirmations,
  status indicators, copyable diagnostics); full-suite review against §6's list; run
  e2e + doctor + manual smoke checklist on this machine; close every §9 item; final
  DEVLOG entry; tag `v0.1.0`.

## 6. Testing requirements

Unit suite (offline, default `uv run pytest`) must cover at minimum: config loading +
derived defaults + precedence; slug generation (incl. the two canonical examples,
reserved names, uniqueness); path safety + `private/` rejection (repo-relative);
bundle parsing (nested fences, empty files, prose between blocks, metadata errors,
every §4.4 rejection); version creation + manifests; dependency policy (all four
flags + approval mode); uv command/env construction + secret redaction; port
allocation (reservation, reuse, exhaustion, atomicity); process state transitions
(full allowed/forbidden map); Caddyfile generation; health-check state transitions;
crash-notification rules (all six never-notify cases, all send conditions, dedupe);
schema inference + diff (added/removed/type-changed); Excel rules (sheet required,
`Sheet1` default, CSV/parquet exempt, sheet stored in metadata, sheet used for
inference and both comparison sides); migrations (fresh + incremental); backup
change-detection + retention; log cleanup; archive + hard-delete flow; SDK behavior
(env resolution, `load_dataset` empty/undefined cases, `require_login` disabled
no-op).

Markers: `integration` (real uv resolution — needs network/uv), `e2e` (full deploy),
`caddy` (needs caddy binary). Each self-skips with a clear reason when prerequisites
are missing.

**E2E scenario (`pytest -m e2e`)** using `examples/sample-bundle.md` (a tiny
Streamlit app that uses `waloader_sdk.datasets`): create app through the service
layer → deploy (real venv, real streamlit process, direct-port mode) → poll health
until `running` → upload a CSV for its concept → replacement upload with a changed
schema → assert diff detected + confirmation path → stop → delete → assert archive
exists → reconcile. A `caddy`-marked variant validates the generated Caddyfile and
round-trips a proxied request when a caddy binary is present.

## 7. Documentation deliverables

`docs/configuration.md`, `docs/process-management.md`, `docs/caddy-reverse-proxy.md`,
`docs/markdown-bundle-contract.md`, `docs/dataset-concepts-contract.md`,
`docs/user-management.md`, `docs/dependency-policy.md`, `docs/troubleshooting.md`,
plus: `docs/llm-bundle-prompt.md` (a copy-paste prompt template finance users give
their LLM to emit a valid bundle — includes the fence-length rule, SDK usage patterns
with graceful local fallbacks, and Dataset Concept `[bracket]` conventions),
`docs/manual-smoke-checklist.md` (the human UI walkthrough for release verification),
and `README.md` (what/why + quickstart per OS + doctor + serve). Practical and
operator-focused; every CLI documented with examples; shell examples bash/zsh first,
then PowerShell; deployment-to-RHEL runbook inside `docs/troubleshooting.md` or the
README (copy repo → write local `config/waloader.toml` → `doctor` → `serve`).

## 8. Verification on this machine (and later on RHEL)

This Mac has uv 0.7.20, Python 3.12.2, and Caddy v2.11.4 (`/opt/homebrew/bin/caddy`).
`caddy`-marked tests must therefore **run, not skip, on this machine**, and the
manual smoke checklist must be walked in both modes: Caddy enabled (clean
`/waloader` and `/apps/<slug>` URLs) and direct-port mode. Tests still self-skip
with a reason on machines without a caddy binary. Full check sequence (all must
pass for §9):

```bash
uv run ruff check .
uv run pytest                # offline unit suite
uv run pytest -m integration # uv preflight against live index
uv run pytest -m e2e         # real deployment round-trip
uv run python -m waloader.tools.doctor
uv run python -m waloader.tools.serve   # then walk docs/manual-smoke-checklist.md
```

On the Red Hat box later: clone/copy repo, write local config, run `doctor`, rerun
the same sequence. No code changes expected — only `config/waloader.toml`.

## 9. Definition of Done (all must hold)

1. Every capability in §4 is implemented and traceable to passing tests (§6 list
   fully covered); no feature exists only in the UI without a service-layer path.
2. All phases P0–P14 checked off in `PROGRESS.md` with per-phase commits.
3. `uv run pytest` (unit), `-m integration`, `-m e2e`, and `-m caddy` all green on
   this machine (Caddy is installed here — caddy tests may not skip); `ruff check .`
   clean.
4. `doctor` passes on this machine; `serve` brings up WALoader; the manual smoke
   checklist has been walked against the running instance (create → deploy sample
   bundle → open app → dataset upload + replacement diff → stop/resume/restart →
   update code → delete; admin panels; user management on a child app).
5. All §7 docs exist and match behavior; every setting documented per §4.18; no
   undocumented settings.
6. No plaintext passwords; secret redaction tested; no `private/` violation; nothing
   secret committed.
7. `PROGRESS.md` shows no open blockers; `DEVLOG.md` has a final entry; repo tagged
   `v0.1.0`.

## Appendix A — expected DB tables (builder may refine columns, not drop concepts)

`schema_migrations`, `users` (platform users, argon2 hashes, email, is_admin,
is_active), `apps` (identity, owner, slug, name, description, state, current_version,
port, caddy_route, user_mgmt_enabled, soft-delete fields, purge_after),
`app_versions` (number, manifest, bundle path), `deployments` (attempt history:
kind, status, timings, error summary, log path), `app_runtime` (pid, pid_create_time,
started_at, last_healthy_at, last_failure_reason), `dataset_concepts`,
`dataset_files` (original + canonical paths, sheet_name, schema_json, is_current),
`app_users` (+ observations), `app_user_attachments`, `settings` (admin overrides),
`notifications_sent` (dedupe), `audit_log`.
