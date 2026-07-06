# Process management

WALoader manages child Streamlit apps as **detached OS processes**
(`start_new_session` on macOS/Linux, `CREATE_NEW_PROCESS_GROUP |
DETACHED_PROCESS` on Windows), so they keep running when WALoader itself
restarts. Process identity is `pid` **plus** process creation time — a reused
PID after a reboot never matches.

## States

`created → deploying → running / deployment_failed`, plus `stopped`, `failed`
(health checks), `pending_delete → deleted`. All transitions go through one
enforced state machine; the dashboard badges mirror these states.

## Lifecycle operations

UI: dashboard card → gear icon (stop / resume / restart / delete, each with a
confirmation). CLI equivalents (same service layer):

bash/zsh (macOS/Linux):

```bash
uv run python -m waloader.tools.appctl list
uv run python -m waloader.tools.appctl status  client-positions
uv run python -m waloader.tools.appctl start   client-positions
uv run python -m waloader.tools.appctl stop    client-positions
uv run python -m waloader.tools.appctl restart client-positions
uv run python -m waloader.tools.appctl logs    client-positions --kind runtime --lines 200
uv run python -m waloader.tools.appctl health  client-positions
uv run python -m waloader.tools.appctl reconcile
```

PowerShell (Windows):

```powershell
uv run python -m waloader.tools.appctl list
uv run python -m waloader.tools.appctl status client-positions
# (identical subcommands)
```

- `stop` terminates the app's whole process tree gracefully, then kills after
  `apps.stop_timeout_seconds` (default 10 s). User-triggered stops never send
  crash emails.
- `start`/resume revalidates the app's port (keeps it when possible), launches
  the current version from its per-version venv, and waits for the first
  healthy response.
- `logs` tails the current version's `runtime.log` / `deploy.log` / `test.log`
  from `data/logs/apps/<slug>/<version>/`.

## Health checks

While WALoader runs (and `health.background_enabled = true`), every
`health.interval_seconds` each running app is probed: process alive → TCP port
open → HTTP `GET /_stcore/health`. A dead process fails the app immediately;
transient unhealthiness must repeat `health.consecutive_failures_threshold`
times. A `running → failed` transition triggers the crash-notification rules
(`docs/user-management.md` § notifications).

**Limitation (accepted, by design):** automatic health checks and maintenance
run only while WALoader itself is up. `appctl health` / `reconcile` cover the
gaps manually.

## Running WALoader itself as a daemon (survives SSH logout)

Foreground `serve` dies with your terminal. On servers, use daemon mode:

bash/zsh (macOS/Linux):

```bash
uv run python -m waloader.tools.serve --daemon    # detached; logs to data/logs/waloader/serve.log
uv run python -m waloader.tools.serve --status
uv run python -m waloader.tools.serve --stop      # child apps keep running
```

PowerShell (Windows): identical commands.

The daemon runs in its own session with no controlling terminal, so a dropped
SSH connection cannot SIGHUP it — no `nohup`/`tmux` gymnastics needed. It is
tracked by a pidfile (pid + creation time) at `data/waloader.pid.json`.

**If it still dies when you log out** (some hardened RHEL images set
`KillUserProcesses=yes` in systemd-logind, which kills *all* of a user's
processes at logout — including detached ones and your child apps), the fix
is lingering: `loginctl enable-linger $USER` (may require an admin). Verify
with `loginctl show-user $USER | grep Linger`.

## Startup reconciliation

At every WALoader start (serve and UI boot), DB state is compared with
reality: apps recorded as running whose processes are gone become `stopped`
and are listed as **resume candidates** (admin panel → Processes → resume
selected/all); stopped apps whose recorded process is somehow alive are
re-adopted as `running`. A WALoader restart therefore never emails anyone.

## Port allocation

Ports come from `ports.child_app_start`–`ports.child_app_end` (default
8601–8999): DB reservation plus a real bind test, atomically; an app keeps its
port across restarts/updates whenever possible; hard-deleting an app frees its
port.
