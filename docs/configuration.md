# WALoader configuration reference

WALoader reads TOML configuration with this precedence (lowest → highest):

1. Built-in defaults (shown below)
2. TOML file: explicit path given to `load_config`, else `$WALOADER_CONFIG`,
   else `config/waloader.toml` if present
3. DB-stored settings (edited in the admin panel) — everything **except `[paths]`**,
   which is bootstrap-only because the database location derives from it

Unknown keys are a **hard startup error** — there are no undocumented settings.
The admin panel shows every setting's *effective* value and its source
(`default` / `toml` / `db`).

Copy `config/waloader.example.toml` to `config/waloader.toml` (git-ignored) for
per-machine values.

bash/zsh (macOS/Linux):

```bash
cp config/waloader.example.toml config/waloader.toml
```

PowerShell (Windows):

```powershell
Copy-Item config\waloader.example.toml config\waloader.toml
```

Legend for each setting below — **Default**, **Required**, **Allowed values / effect**,
**Change when**, **If missing**, **If wrong**, **Secrets?**, **Commit-safe?**
"If missing" always means: the default applies (settings are only *required* where
stated). None of these values may contain passwords or tokens unless noted.

---

## [server]

### `server.public_host`
- **Purpose:** hostname shown in generated app URLs (Caddy clean URLs and direct-port URLs).
- **Default:** `"localhost"` · **Required:** no
- **Allowed values / effect:** any hostname/IP your users can reach; used for display and
  Caddy site addresses, not for binding.
- **Change when:** deploying on a server users reach by name (e.g. `finbox01`).
- **If wrong:** links shown in the UI point at an unreachable host; apps still run.
- **Secrets?** no · **Commit-safe?** yes (example file); real hostnames are usually fine.

## [paths]

### `paths.data_dir`
- **Purpose:** root for ALL runtime state (DB, apps, venvs, logs, backups, archives,
  uploads, uv cache, Caddyfile).
- **Default:** `"data"` (relative to the working directory) · **Required:** effectively
  yes in production — set it explicitly.
- **Allowed values / effect:** any writable directory; relative paths resolve against the
  working directory WALoader starts from.
- **Change when:** production (e.g. `/srv/waloader/data`), or to keep state on a bigger disk.
- **If wrong:** unwritable → startup fails with a clear error; pointing at a different
  directory "loses" (but does not delete) previous state.
- **Secrets?** no · **Commit-safe?** the setting is; the directory itself is git-ignored.

### `paths.database_path`, `paths.apps_dir`, `paths.logs_dir`, `paths.backups_dir`,
### `paths.archives_dir`, `paths.uploads_dir`, `paths.tmp_dir`, `paths.uv_cache_dir`,
### `paths.caddy_config_path`, `paths.caddy_logs_dir`
- **Purpose:** explicit overrides of the derived layout (see example file for the
  derivation table).
- **Default:** `""` = derive from `data_dir` · **Required:** no
- **Allowed values / effect:** absolute or relative paths; empty string derives.
- **Change when:** almost never — e.g. DB on a faster disk, logs on a bigger one.
- **If wrong:** same failure modes as `data_dir`, scoped to that one path.
- **Secrets?** no · **Commit-safe?** yes.

## [executables]

### `executables.python_binary`
- **Purpose:** Python 3.12 interpreter used to create child app virtualenvs.
- **Default:** `""` → `shutil.which("python3.12")`, else WALoader's own interpreter.
- **Required:** only if python3.12 is not on PATH.
- **Allowed values / effect:** absolute path to a Python ≥3.12 binary; all child venvs
  are created from it.
- **Change when:** multiple Pythons installed, or python3.12 not on PATH (typical on
  Windows and RHEL).
- **If wrong:** `doctor` fails; deployments fail at venv creation with the path in the error.
- **Secrets?** no · **Commit-safe?** machine-specific — keep real values in the local file.

### `executables.uv_binary`
- **Purpose:** the uv executable used for venv creation, installs, preflight.
- **Default:** `"uv"` (PATH lookup) · **Required:** only if uv is not on PATH.
- **If wrong:** `doctor` fails; deployments fail before installing anything.
- **Secrets?** no · **Commit-safe?** machine-specific paths → local file.

### `executables.caddy_binary`
- **Purpose:** the Caddy executable for the reverse proxy.
- **Default:** `""` → `shutil.which("caddy")` · **Required:** only when `caddy.enabled = true`.
- **If missing while enabled:** Caddy operations fail with a clear error; apps still
  reachable via direct ports.
- **Secrets?** no · **Commit-safe?** machine-specific paths → local file.

## [uv]

### `uv.config_file`
- **Purpose:** PATH to a uv config file holding private package index configuration
  (corporate). Exported as `UV_CONFIG_FILE` for every uv invocation.
- **Default:** `""` (unset — public PyPI) · **Required:** only on corporate networks.
- **SECURITY:** the file's **contents are secret** and typically contain credentials.
  WALoader references the path only and never reads, prints, or logs the contents.
  Never commit the file itself.
- **If wrong:** uv cannot resolve packages; preflight and deployments fail with a
  copyable (credential-redacted) error.
- **Secrets?** the *referenced file* yes; the path itself is usually harmless.
- **Commit-safe?** the path may reveal usernames (e.g. `C:\Users\bl81398\uv.toml`) —
  keep it in the local, git-ignored config.

### `uv.cache_dir`
- **Purpose:** shared uv download/wheel cache for all child venvs (speeds up deploys).
- **Default:** `""` → `<data_dir>/uv-cache`. **If wrong:** slower installs or permission
  errors at install time. **Secrets?** no · **Commit-safe?** yes.

### `uv.system_certs`
- **Purpose:** make uv trust the OS certificate store (`UV_SYSTEM_CERTS=true`).
- **Default:** `false` · **Required:** `true` on corporate machines with TLS-intercepting
  proxies/artifact repositories.
- **If wrong:** `false` where needed → TLS errors during preflight/install; `true`
  elsewhere → harmless.
- **Secrets?** no · **Commit-safe?** yes.

### `uv.ssl_cert_file` / `uv.ssl_cert_dir`
- **Purpose:** custom CA bundle exported as `SSL_CERT_FILE`/`SSL_CERT_DIR`.
- **Default:** `""` · **Change when:** the operator provides a CA bundle instead of
  (or besides) `system_certs`. **If wrong:** TLS failures at resolve time.
- **Secrets?** certificates are not secrets, but treat corporate bundles as internal.
- **Commit-safe?** the paths yes; the bundle files no.

### `uv.allow_insecure_hosts`
- **Purpose:** hosts uv may contact without TLS verification (`--allow-insecure-host`).
- **Default:** `[]` · **Change when:** explicitly instructed by the operator. Prefer
  fixing certificates. **If wrong:** either TLS failures persist (host missing) or
  you've disabled verification needlessly.
- **Secrets?** no · **Commit-safe?** yes, but keep environment-specific.

### `uv.preflight_packages`
- **Purpose:** packages the preflight check resolves (`uv pip install --dry-run …`)
  to prove index connectivity before deployments.
- **Default:** `["pandas"]` · **Change when:** your index mirrors a different set.
- **If wrong:** preflight fails even though deployment could succeed (or vice versa).
- **Secrets?** no · **Commit-safe?** yes.

## [ports]

### `ports.waloader_port`
- **Purpose:** the port WALoader's own Streamlit UI listens on.
- **Default:** `8501`. **If wrong (occupied):** `serve` fails to bind; pick another.
- **Secrets?** no · **Commit-safe?** yes.

### `ports.child_app_start` / `ports.child_app_end`
- **Purpose:** inclusive allocation range for child app ports.
- **Default:** `8601`–`8999` (≈400 apps). Must satisfy `end >= start` (validated).
- **Change when:** the range collides with other services or you need more apps.
- **If wrong:** allocation failures ("no free port") or collisions with other software.
- **Secrets?** no · **Commit-safe?** yes.

### `ports.caddy_public_port`
- **Purpose:** the single public port Caddy serves clean URLs on.
- **Default:** `8080` (port 80 is deliberately not assumed). **If wrong:** Caddy fails
  to bind (occupied) or users can't reach it (firewall).
- **Secrets?** no · **Commit-safe?** yes.

## [caddy]

### `caddy.enabled`
- **Purpose:** master switch for the reverse proxy.
- **Default:** `false`. `true` → clean URLs, requires a caddy binary; `false` →
  direct-port URLs only, no Caddy processes are touched.
- **If wrong:** `true` without a binary → Caddy ops fail loudly, apps stay reachable
  by port; `false` unexpectedly → users see port URLs.
- **Secrets?** no · **Commit-safe?** yes.

### `caddy.admin_port`
- **Purpose:** localhost port for Caddy's admin API; used by `reload`.
- **Default:** `2019`. **Change when:** another Caddy/process owns 2019.
- **If wrong:** reload/stop-via-admin fail; restart Caddy to apply config instead.
- **Secrets?** no · **Commit-safe?** yes.

## [dependencies_policy]

All five settings govern what child apps may declare in `pyproject.toml`
(`[project.dependencies]`); violations abort deployment with the offending
requirement named. All are **Commit-safe**, **no secrets**.

### `allow_app_dependencies` — default `true`
`true`: apps may declare normal PyPI dependencies. `false`: apps may only use the
approved base set (`apps.base_dependencies`); any `pyproject.toml` dependency is
rejected. Set `false` for a locked-down platform.

### `allow_direct_url_dependencies` — default `false`
`true`: allow `pkg @ https://...` direct wheel/sdist URLs. `false`: reject them.
Keep `false` unless you trust arbitrary URLs.

### `allow_vcs_dependencies` — default `false`
`true`: allow `git+https://...` style requirements. `false`: reject.

### `allow_path_dependencies` — default `false`
`true`: allow local path / `file://` requirements. `false`: reject. Keep `false`;
local paths rarely exist on the server.

### `require_admin_approval_for_new_dependencies` — default `false`
`true`: any dependency not previously approved for that app blocks deployment in a
clearly reported state until an admin approves it (admin panel), then retry succeeds.
`false`: install is attempted automatically and failures are reported.

## [uploads]

### `uploads.max_markdown_bundle_mb` — default `10`
Max markdown bundle size. Too small → legitimate bundles rejected; huge → slow parses.

### `uploads.max_dataset_file_mb` — default `250`
Max dataset upload size. Streamlit's own uploader limit is raised to match.

### `uploads.max_bundle_files` — default `200`
Max file blocks per bundle; guards against runaway generated bundles.

### `uploads.allowed_dataset_extensions` — default `[".csv", ".xlsx", ".xls", ".parquet"]`
Accepted dataset formats. Removing one hides it from upload UIs and rejects it in the
service layer. Adding new ones requires code support — don't.

### `uploads.default_excel_sheet_name` — default `"Sheet1"`
Pre-filled sheet name for Excel uploads (sheet name is required for `.xlsx`/`.xls`).

All: **no secrets**, **commit-safe**.

## [health]

### `health.interval_seconds` — default `30`
Background check cadence. Lower = faster crash detection, more overhead.

### `health.http_timeout_seconds` — default `5`
Timeout for the HTTP probe (`/_stcore/health`). Too low → false failures on slow boxes.

### `health.grace_period_seconds` — default `180`
An app must survive this long past initial health before a later death counts as a
*production crash* (crash-email eligible). Too low → deploy-time flakiness emails;
too high → real crashes shortly after deploy are not emailed.

### `health.consecutive_failures_threshold` — default `3`
Failed checks in a row before a running app is marked `failed`.

### `health.initial_check_timeout_seconds` — default `30`
How long deployment polls for the first healthy response before declaring
`deployment_failed`. Raise on slow machines.

### `health.background_enabled` — default `true`
Runs the background worker (periodic health checks + once-daily maintenance:
backup, retention cleanup, expired-app purge) inside the WALoader UI process.
`false` → nothing runs automatically; use the maintenance CLI or admin panel.
Note the documented limitation either way: automatic checks only happen while
WALoader itself is running.

All: **no secrets**, **commit-safe**.

## [notifications]

### `notifications.crash_emails_enabled` — default `true`
Master switch for crash emails. The shipped `send_mail` only logs (see
`docs/user-management.md` / `src/waloader/notifications/mailer.py`) — at work,
replace that one function body with the corporate mailer.

### `notifications.sender` — default `"waloader@localhost"`
Sender address passed to `send_mail`. Change to the address your mail system expects.
**If wrong:** corporate mailers may reject the message.

### `notifications.admin_cc` — default `[]`
Extra recipients CC'd on every crash email (e.g. the operator). Email addresses are
internal data — fine in the local config, avoid committing real ones.

## [retention]

### `retention.backup_days` — default `183`
Days DB backups are kept before cleanup deletes them.

### `retention.log_days` — default `183`
Age limit for log files during `cleanup-logs`.

### `retention.deleted_app_days` — default `183`
Days a soft-deleted app's archive (and slug reservation) survives before hard delete.
Lower it to reclaim disk sooner; raising it does not resurrect already-purged apps.

### `retention.factory_reset_backup_days` — default `183` (~6 months)
How long the full backup taken automatically before a **factory reset**
(`data/backups/factory/…`) is kept; daily maintenance prunes older ones.
Manual backups under `data/backups/manual/` are deliberate operator artifacts
and never expire automatically. Raising this after a reset extends the
remaining life of existing factory backups (pruning is age-based).

All: **no secrets**, **commit-safe**.

## [database]

### `database.auto_migrate` — default `true`
Run pending SQL migrations at startup. `false` → operator must run
`python -m waloader.tools.db migrate` manually; startup refuses to run with a
pending-migration DB.

## [debug]

### `debug.rich_tracebacks_enabled` — default `true`
Pretty Rich tracebacks in console output. Cosmetic only.

### `debug.rich_tracebacks_show_locals` — default `false`
Include local variables in tracebacks. **Keep `false` in production** — locals can
contain sensitive values (this is why the default is off).

## [apps]

### `apps.bind_address` — default `"auto"`
Address child apps bind: `"auto"` = `127.0.0.1` when Caddy is enabled (only the proxy
reaches apps) and `0.0.0.0` in direct mode (users reach ports directly). Explicit
`"127.0.0.1"`/`"0.0.0.0"` override. **If wrong:** apps unreachable (loopback in direct
mode) or exposed unnecessarily.

### `apps.stop_timeout_seconds` — default `10`
Graceful terminate window before the process tree is killed.

### `apps.test_timeout_seconds` — default `300`
Timeout for an uploaded app's own pytest run during deployment; exceeding it fails
the deployment with a clear message.

### `apps.base_dependencies` — default `["streamlit", "pandas", "plotly", "duckdb", "pyarrow"]`
Installed into a child venv when the app has **no** `pyproject.toml`; also the entire
allowed universe when `allow_app_dependencies = false`.

### `apps.sdk_dependencies` — default `["argon2-cffi"]`
Always installed into child venvs — `waloader_sdk.auth` verifies argon2 hashes inside
the child process. Remove nothing here unless you disable user management everywhere.

All: **no secrets**, **commit-safe**.
