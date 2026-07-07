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
