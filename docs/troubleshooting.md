# Troubleshooting & deployment runbook

## First move, always

bash/zsh (macOS/Linux):

```bash
uv run python -m waloader.tools.doctor            # full check (needs network)
uv run python -m waloader.tools.doctor --offline  # skip the uv preflight
```

PowerShell (Windows): identical. Doctor verifies config, Python/uv/Caddy
binaries, data-dir writability, DB migrations, a free child port, and uv index
connectivity — with exact failure details per line.

## Deploying to a new machine (e.g. the Red Hat box)

1. Copy the repository (or `git clone`). Install uv
   (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and ensure a Python
   3.12 binary exists.
2. `uv sync` (uses your configured index; on corporate networks set the uv
   config file first — see step 3).
3. `cp config/waloader.example.toml config/waloader.toml` and set at minimum:
   `paths.data_dir` (e.g. `/srv/waloader/data`), `server.public_host`,
   `executables.python_binary`, and on corporate networks `uv.config_file`
   (path only — never commit that file) + `uv.system_certs = true`. For clean
   URLs also `executables.caddy_binary` + `[caddy] enabled = true`.
4. `uv run python -m waloader.tools.doctor` until everything passes.
5. `uv run python -m waloader.tools.users create-admin <you>`
6. `uv run python -m waloader.tools.serve` (keep it in tmux/screen — WALoader
   deliberately does not require systemd; child apps survive WALoader
   restarts either way).
7. Optional: `uv run python -m waloader.tools.caddyctl start`.

No code changes are expected between macOS, Windows 11, and RHEL — config
only. Same suite proof: `uv run pytest && uv run pytest -m "integration or e2e"`.

## Common problems

**Deployment failed — dependency installation.** The error block (copy it!)
contains the redacted uv output. Usual causes: package name typo in the
bundle's pyproject, private index unreachable (`doctor` → preflight), TLS
(`uv.system_certs = true` on corporate machines). Retry via the error panel
after fixing.

**Deployment failed — the app's own tests failed.** The pytest output is in
the error block and in `data/logs/apps/<slug>/<version>/test.log`. Paste it
to the coding LLM, get a fixed bundle, retry-upload.

**App shows "did not become healthy".** Read the runtime.log tail in the
error block (crash at import time is the classic — missing dependency,
syntax error). Full log: `appctl logs <slug>`.

**App runs but the page 404s behind Caddy.** Regenerate + reload
(`caddyctl reload` or admin panel); confirm the app was (re)deployed *after*
Caddy was enabled so it launched with the right `--server.baseUrlPath`.

**"No free port available".** Widen `[ports]` or stop unused apps; `appctl
list` shows current allocations.

**WALoader restarted and apps show stopped.** That's reconciliation being
honest — the processes died while WALoader was down (or the machine
rebooted). Admin panel → Processes → resume selected/all, or `appctl start`.

**Uploads rejected as too large.** Raise `uploads.max_markdown_bundle_mb` /
`uploads.max_dataset_file_mb` (admin panel → Configuration) and restart
`serve` so Streamlit's own upload cap follows.

**Crash emails not arriving.** By design the shipped mailer only logs (grep
`MAIL (stub` in `data/logs/waloader/app.log`). At work, replace the body of
`send_mail` in `src/waloader/notifications/mailer.py` with the corporate
mailer. Also check: owner email set, grace period elapsed, app had passed
initial health.

**Database locked / busy.** Connections use WAL + 5 s busy timeout; sustained
lock errors usually mean the data dir is on a network share — keep
`paths.data_dir` on local disk.

## Where things live

```text
data/waloader.db                              SQLite (WAL)
data/apps/<slug>/versions/<n>/source/         reconstructed code
data/apps/<slug>/runtime/venvs/<n>/           per-version virtualenv
data/apps/<slug>/datasets/<concept>/          originals + current.parquet
data/logs/waloader/app.log · error.log        platform logs
data/logs/apps/<slug>/<n>/{deploy,runtime,test}.log
data/logs/caddy/                              caddy.log · access.log
data/backups/                                 waloader-<ts>.db (+ .sha256)
data/archives/                                deleted-app zip archives
```

Retention defaults: backups/logs/deleted apps 183 days
(`[retention]`).
