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
