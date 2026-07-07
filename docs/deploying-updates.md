# Updating a running WALoader server

Updating an installed server means shipping new **code** while leaving your
**runtime state** (`data/`, `config/waloader.toml`, `.venv`) completely alone.
`python -m waloader.tools.deploy` does this in one command — no git needed on
the server.

## Why not just re-scp a zip?

A naive unzip-over-the-top has three failure modes the tool removes:

1. **Config clobbering** — if the archive ever contains a
   `config/waloader.toml` (easy on a dev machine), unzip overwrites the
   server's real one. The tool's payload is **git-tracked files only**, so
   your git-ignored config/data/venv are never in it — plus a hardcoded
   protected-path denylist refuses to touch them even if force-added to git.
2. **Stale files** — unzip only adds; it never removes a file that a new
   version deleted or renamed, leaving dead code behind. The tool diffs a
   per-deploy manifest and cleans those up (never protected paths).
3. **Forgotten follow-up** — the tool runs `uv sync`, `db migrate`, and
   restarts the daemon for you.

## One-time setup (on your Mac/Windows)

```bash
cp config/deploy.example.toml config/deploy.toml   # git-ignored
```

Edit `config/deploy.toml` with your server's host, user, and the absolute
project path on the box (`remote_dir`). Auth uses your existing `ssh`/`scp`:
key/agent = unattended; password = you'll be prompted once. If a
non-interactive ssh session can't find `uv`, set `uv` to its absolute path.

## Push an update

bash/zsh (macOS/Linux) — PowerShell identical:

```bash
uv run python -m waloader.tools.deploy push --dry-run   # preview: no changes
uv run python -m waloader.tools.deploy push             # do it
```

`push` packages the git-tracked files here, scp's the tarball (and the
apply script) to the box, then remotely: syncs files, cleans up stale ones,
runs `uv sync` → `db migrate` → restarts the `serve` daemon. Child apps keep
running throughout; the WALoader UI comes back on the new version.
`--no-restart` updates files only (prints the restart command);
`--no-migrate` skips migrations.

**Run WALoader via `serve --daemon`, not in the foreground.** The auto-restart
manages the daemon (pidfile-tracked). If a foreground `serve` is holding the
port, the restart can't stop it and the new daemon fails to bind — stop the
foreground one yourself, or switch to `serve --daemon`
(see `docs/process-management.md`).

### Windows: "ssh not found" / crashes on push

`push` uses your system `ssh`/`scp`. If your Python can't see them (common
when you reach the box via **Cmder**, Git Bash, or PuTTY rather than Windows
OpenSSH), push stops with a clear message. Point deploy at the client you
already use — for Cmder/Git-for-Windows, its bundled `ssh.exe`/`scp.exe`:

```toml
# config/deploy.toml
ssh = "C:/Users/you/tools/cmder/vendor/git-for-windows/usr/bin/ssh.exe"
scp = "C:/Users/you/tools/cmder/vendor/git-for-windows/usr/bin/scp.exe"
```

(Or install the Windows OpenSSH client, or skip push and use the manual
**package + apply** flow below — it needs no SSH automation.)

### Corporate / offline server: `uv sync` must use your private index

On a locked-down box `uv sync` must reach your **private package index**, not
`pypi.org` — otherwise it fails with `Name or service not known` /
`Failed to fetch hatchling`. deploy handles this automatically: on the server
it **reads the box's own `config/waloader.toml` `[uv]` settings**
(`config_file`, `system_certs`, `ssl_cert_file`) and sets the matching
`UV_CONFIG_FILE` / `UV_SYSTEM_CERTS` / `SSL_CERT_FILE` for `uv sync`. So if
your child-app deployments already work on that box, updates work with no
extra config.

If you need to override or add variables (or the box's config doesn't cover
it), set them in `config/deploy.toml`:

```toml
[remote.env]
UV_CONFIG_FILE = "/home/you/uv.toml"
UV_SYSTEM_CERTS = "true"
```

The **server uv binary** is resolved the same way: unset in `deploy.toml` →
read from the box's `config/waloader.toml` `[executables].uv_binary`, else
plain `uv` on PATH. Set `uv = "/home/you/.local/bin/uv"` (a **Linux** path)
only to override, e.g. if a non-interactive ssh session can't find it.

### Which values are local vs. server?

`deploy.toml`'s `[remote]` holds both:

- **Local** (run on your machine): `ssh`, `scp` — your Windows/Cmder client.
- **Server** (run on the box, Linux paths): `remote_dir`, `uv`,
  `remote_python`, `[remote.env]`.

The server values all **default to the box's own `config/waloader.toml`** so
you don't repeat yourself; anything you set in `deploy.toml` **overrides** it.
The Python binary and per-app uv settings WALoader uses to build *child apps*
are unchanged — deploy only reuses them to make its own `uv sync` reach your
index.

## Server-side apply (if you prefer manual scp)

Don't want SSH automation? Build the tarball here and apply it on the box —
a safe replacement for unzip:

```bash
# here:
uv run python -m waloader.tools.deploy package --out /tmp/waloader-update.tar.gz
scp /tmp/waloader-update.tar.gz you@box:/tmp/

# on the box (survives with stock python3; no git needed):
python3 -m waloader.tools.deploy apply /tmp/waloader-update.tar.gz \
    --root /path/to/waloader --dry-run          # preview
python3 -m waloader.tools.deploy apply /tmp/waloader-update.tar.gz \
    --root /path/to/waloader                     # apply + uv sync + migrate + restart
```

## Safety guarantees (what it will never do)

- Never overwrites or deletes `config/waloader.toml`, `config/deploy.toml`,
  anything under `data/`, `private/`, `.venv/`, or `.git/`.
- Refuses to apply into a directory that isn't already a WALoader install
  (no `pyproject.toml` → abort).
- Aborts if the payload is missing sentinel files (`pyproject.toml`,
  `src/waloader/__init__.py`) — guards against wiping the server with an
  empty/garbage archive.
- `--dry-run` on both `push` and `apply` shows exactly what would change,
  including every file that would be deleted, without touching anything.

## First run establishes the baseline

The first managed `apply`/`push` on a server that was set up manually writes
`.deploy/manifest.json`. Stale-file cleanup starts from the *next* update
(the first one only adds/updates, since there's no prior manifest to diff).
