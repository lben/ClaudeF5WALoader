# Caddy reverse proxy

Caddy gives clean URLs on one public port instead of one port per app:

```text
http://<host>:8080/waloader            → the WALoader UI
http://<host>:8080/apps/<app-slug>     → each deployed app
```

Without Caddy (default, `caddy.enabled = false`) everything still works on
direct ports: `http://<host>:<app-port>`. If Caddy fails at deploy time,
WALoader reports the error and keeps serving direct URLs.

## Setup

1. Get a Caddy binary (no root needed, no global install):
   - macOS: `brew install caddy`
   - Windows: download `caddy_windows_amd64.exe`
   - RHEL: download the static binary from caddyserver.com and `chmod +x`
2. Point the config at it (or leave empty if `caddy` is on PATH) and enable:

   ```toml
   [executables]
   caddy_binary = "/opt/homebrew/bin/caddy"

   [caddy]
   enabled = true

   [server]
   public_host = "yourservername"
   ```
3. Restart `serve` (children pick up `--server.baseUrlPath` on their next
   deploy/restart), then start Caddy from the admin panel → Caddy → **Start**,
   or:

   bash/zsh:

   ```bash
   uv run python -m waloader.tools.caddyctl generate
   uv run python -m waloader.tools.caddyctl validate
   uv run python -m waloader.tools.caddyctl start
   ```

   PowerShell: identical commands.

## How it works

- The Caddyfile is **generated from the app database** into
  `data/caddy/Caddyfile` — never hand-edit it; regenerate instead. Routes:
  `redir / /waloader/`, `/waloader*` → the WALoader port, `/apps/<slug>*` →
  each app's port (path-preserving reverse proxy; WebSockets work natively).
- Caddy runs as a detached child of WALoader, tracked by a pidfile with
  creation time; its admin API listens on `localhost:<caddy.admin_port>`
  (default 2019) and is used by `reload`.
- Every successful deployment/deletion refreshes routes automatically
  (regenerate + hot reload when Caddy is running).
- Logs: `data/logs/caddy/caddy.log` (process) and `access.log` (requests),
  viewable in the admin panel.
- Port 80 is deliberately not assumed; the public port default is **8080**
  (`ports.caddy_public_port`).

## Commands

```bash
uv run python -m waloader.tools.caddyctl generate   # write the Caddyfile
uv run python -m waloader.tools.caddyctl validate   # caddy validate --config ...
uv run python -m waloader.tools.caddyctl start
uv run python -m waloader.tools.caddyctl stop
uv run python -m waloader.tools.caddyctl reload     # regenerate + hot reload
uv run python -m waloader.tools.caddyctl status
```

The admin panel (Admin → Caddy) exposes the same operations plus the current
generated Caddyfile and log tails.

## Behavior matrix

| caddy.enabled | Caddy running | URLs shown to users            | App bind address |
|---------------|---------------|--------------------------------|------------------|
| false         | —             | `http://host:<port>`           | 0.0.0.0          |
| true          | yes           | `http://host:8080/apps/<slug>` | 127.0.0.1        |
| true          | no/crashed    | clean URLs unreachable — start Caddy; direct ports still work only if `apps.bind_address` is overridden to `0.0.0.0` | 127.0.0.1 |
