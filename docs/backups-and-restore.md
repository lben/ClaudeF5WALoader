# Backups, restore, app migration, factory reset

All of this is service-first: `backupctl` / `appctl` on the command line and
**Admin → Backups & reset** in the UI drive the same code. Archives are plain
zips with a JSON manifest — inspectable with any zip tool.

## Where archives live (and why)

```text
data/backups/manual/    your scoped backups + app exports  (never auto-deleted)
data/backups/factory/   automatic pre-factory-reset backups (pruned after
                        retention.factory_reset_backup_days, default 183 ≈ 6 months)
data/archives/          soft-delete archives (pruned with the deleted app)
```

`backups/` is the one subtree that **factory reset and `restore --force`
preserve** — safety copies survive the wipe by location, not by luck. Nothing
under `backups/` is ever nested inside another backup.

## Scoped manual backups

bash/zsh (macOS/Linux) — PowerShell is identical:

```bash
uv run python -m waloader.tools.backupctl create --scope all [--with-logs]
uv run python -m waloader.tools.backupctl create --scope db
uv run python -m waloader.tools.backupctl create --scope apps [--code-only]
uv run python -m waloader.tools.backupctl create --scope app --app <slug> [--code-only]
uv run python -m waloader.tools.backupctl list
```

| Scope | Contains | Restorable how |
|---|---|---|
| `all` | consistent DB snapshot + every app's code/bundles + datasets + user files + deleted-app archives + caddy config (+ logs with `--with-logs`) | `backupctl restore` |
| `db` | the platform database only — users, app metadata, settings, app users, dataset metadata, audit ("admin data") | manually place as `waloader.db` |
| `apps` | every app via the shared archive format; `--code-only` skips datasets/user files | reference/manual |
| `app` | one app — the same format as export, so it **is importable** | `appctl import` |

Never archived, by design: virtualenvs (rebuildable — see Rebuild), the uv
cache, `tmp/`, raw DB files (a consistent snapshot is taken instead), and
`backups/` itself. The UI equivalent (create/download/list/delete) is
**Admin → Backups & reset**.

## Restore (full instance)

```bash
uv run python -m waloader.tools.backupctl restore data/backups/manual/all-<ts>.zip [--force]
```

- Stop `serve` first. Without `--force`, restore refuses if a database already
  exists; with it, the data dir is wiped (except `backups/`) and replaced.
- Only `all`-scope archives restore; app archives go through `appctl import`.
- After restore every app is `stopped` and **needs a rebuild** (venvs are
  never archived): `appctl rebuild --all`, then start apps normally.

## Rebuild

`rebuild` replays the normal deployment pipeline on the app's preserved
byte-exact bundle — same dependency policy, tests, health checks — and
appends a new version:

```bash
uv run python -m waloader.tools.appctl rebuild <slug>     # or --all
```

The dashboard shows "⚠ rebuild required" on affected cards, with a
**Rebuild now** button in the gear dialog. `start` on a venv-less app refuses
with the same instruction rather than half-starting.

## App export / import (migration between instances, and un-delete)

```bash
uv run python -m waloader.tools.appctl export <slug> [--code-only] [--out <dir>]
uv run python -m waloader.tools.appctl import <archive.zip> [--owner <user>] [--name <new name>] [--no-deploy]
```

- The archive carries code (all versions, byte-exact bundles), datasets,
  app users (passwords keep working — argon2 hashes are portable) and their
  attachments. `--code-only` strips datasets/user files.
- Import creates the app fresh: name/slug availability rules are exactly the
  create-screen rules (`--name` to rename); the owner defaults to the archived
  owner's username if it exists locally, else `--owner` is required.
- By default import ends with a rebuild+start; `--no-deploy` leaves it for a
  later `appctl rebuild`.
- **Un-delete:** soft-delete archives under `data/archives/` are the same
  format — import one to resurrect a deleted app (its old name stays reserved
  until retention purges the deleted row, so use `--name`).
- UI: export from the app's gear dialog; import under Admin → Backups & reset
  (admin-only).

## Factory reset

```bash
uv run python -m waloader.tools.backupctl factory-reset            # prompts: type RESET
uv run python -m waloader.tools.backupctl factory-reset --force    # scripted
uv run python -m waloader.tools.backupctl factory-reset --skip-backup --force  # DANGEROUS
```

What it does, in order: stops every child app → writes an audit record →
takes a **full backup including logs** into `data/backups/factory/` (kept
`retention.factory_reset_backup_days` days, default ~6 months, then pruned by
daily maintenance) → stops Caddy → deletes everything under the data dir
except `backups/` → prints the report with undo instructions. Restart `serve`
afterwards; the next start shows the first-run setup screen.

UI: Admin → Backups & reset → Danger zone — the button stays disabled until
you type `RESET` (and the check is enforced server-side too; the UI flow
always takes the safety backup — `--skip-backup` is CLI-only).

**Undo a factory reset:**

```bash
uv run python -m waloader.tools.backupctl restore data/backups/factory/factory-<ts>.zip
uv run python -m waloader.tools.appctl rebuild --all
```

## Moving WALoader to a new machine (migration runbook)

1. Old machine: `backupctl create --scope all --with-logs`; copy the zip and
   your `config/waloader.toml` off the box.
2. New machine: clone the repo, `uv sync`, adjust `config/waloader.toml`
   (paths/binaries for that machine), `python -m waloader.tools.doctor`.
3. `backupctl restore <the zip>` → `appctl rebuild --all` → `serve`.
4. Same accounts, apps, datasets, app users; fresh venvs built from that
   machine's configured index. No code changes — config only.

Single apps migrate the same way with `appctl export` / `appctl import`.

## Retention summary

| What | Kept | Pruned by |
|---|---|---|
| Manual backups (`backups/manual/`) | forever | you (UI delete / filesystem) |
| Factory backups (`backups/factory/`) | `retention.factory_reset_backup_days` (183) | daily maintenance / `cleanup-retention` |
| Daily DB backups (`backups/waloader-*.db`) | `retention.backup_days` (183) | daily maintenance |
| Soft-delete archives (`archives/`) | `retention.deleted_app_days` (183) | daily maintenance |
