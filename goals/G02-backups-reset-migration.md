# Goal G02 — Scoped backups, restore, app migration, factory reset

**Status:** active (G01 is complete and tagged v0.1.0; this goal builds on it).
Read `AGENTS.md` first; everything there still binds. Where this file is
silent, G01's decisions stand (service layer first, CLI + UI as thin clients,
tests per phase, PROGRESS.md/DEVLOG.md at every phase boundary, no
undocumented settings — the doc-sync tests enforce that mechanically).

## 1. Goal statement

Give the WALoader operator real data-lifecycle control:

1. **Scoped manual backups** — one command/click to back up *everything*,
   *only the platform database (admin data)*, or *only apps* (all or one,
   with or without their data), producing self-describing zip archives.
2. **Restore & rebuild** — full-instance restore from an *all* archive, and a
   rebuild path that brings restored/imported apps back to life (venvs are
   never archived, so restore without rebuild would be a broken promise).
3. **Child-app migration** — export any app to a portable archive and import
   it on the same or another WALoader instance (this is also the un-delete
   mechanism for soft-delete archives, which share the same format).
4. **Admin Factory Reset** — stop everything, take a full backup retained
   **183 days (~6 months) by default (configurable)**, wipe the data
   directory back to first-run — behind explicit typed confirmation, CLI
   first with the admin UI built on top (the same pattern as every other
   operation).

Done when every item in §8 passes on this machine.

### Resume protocol

Same as G01: update `PROGRESS.md` (replace the G01 board with a G02 board at
kickoff), append `DEVLOG.md`, and commit at every phase boundary. A fresh
session resumes from `PROGRESS.md`.

## 2. Out of scope

- Incremental/differential backups, encryption, compression tuning, offsite
  copies, scheduled backup *uploads* (the existing daily DB backup job stays
  as is; this goal adds manual scopes on top).
- Partial-table restore (restore is whole-instance; per-app granularity is
  import).
- Importing *platform users* from a full archive into a live instance
  (restore replaces the instance; import carries per-app users only).
- Cross-version archive compatibility promises beyond an `archive_format`
  check with a clear error.
- Any change to child apps' own internal data handling (Dataset Concepts
  already cover that domain).

## 3. Architecture decisions (binding)

1. **One archive format, one builder.** Generalize the soft-delete archive
   into `services/app_archive.py`: `build_app_archive(conn, config, app, *,
   include_data: bool, dest_dir: Path) -> Path`. Metadata becomes
   `archive_format = 2` and now includes everything import needs: the app
   row, versions, dataset concepts + current-file rows, app_users rows
   (argon2 hashes are portable), attachment rows, and the user_mgmt flag.
   Deletion switches to this builder, so **existing soft-delete archives
   gain importability going forward**. Venvs (`runtime/`) are never archived
   — they are rebuildable by definition.
2. **Backups survive the wipe by location, not by luck.** All manual/factory
   archives live under the existing backups tree:
   `data/backups/manual/<scope>-<ts>.zip` and
   `data/backups/factory/factory-<ts>.zip`. Factory reset (and
   `restore --force`) wipes everything under `data_dir` **except
   `backups/`**. No new path outside the data tree, no lost archives.
3. **The filesystem is the registry.** Backups are listed by reading the
   backups tree (name, scope, size, mtime, manifest summary) — never the DB,
   which a reset deletes. Each zip contains `manifest.json`
   (`archive_format`, scope, created_at, WALoader version, effective-config
   dump [config contains no secret *values* by contract — the uv config
   file's *contents* are, as always, never read], and per-app inventories).
4. **Full/db scopes snapshot the DB consistently** via the sqlite backup API
   (same mechanism as the daily job), stored inside the zip as
   `waloader.db`; never a raw copy of a live DB file.
5. **Rebuild = the deployment pipeline on the preserved bundle.** New service
   `rebuild_app(conn, config, app, *, actor)` runs the normal deploy pipeline
   (`kind="rebuild"`) on the current version's byte-exact
   `uploaded_bundle.md`. Import and post-restore recovery both use it —
   no second install path. `lifecycle.start` on an app whose venv is missing
   fails with the explicit message "venv missing — rebuild required" (never a
   crash), and the UI/CLI surface the rebuild action.
6. **Factory reset is a service** (`services/factory_reset.py`) with the CLI
   and UI as thin clients: stop all child apps (every state) → stop Caddy →
   full backup (scope *all*, logs included — reset backups are complete) into
   `backups/factory/` → wipe per decision 2 → report (backup path, purge
   date, leftovers it could not delete, next steps). `--skip-backup` exists,
   is loudly documented as dangerous, and still requires confirmation.
7. **Confirmation contract.** CLI: interactive prompt requiring the literal
   word `RESET` (typed), or `--force` for scripted use; both paths log to the
   audit trail *before* acting (the audit row itself is about to be wiped —
   it also goes into the backup). UI: admin-only danger zone; a text input
   must equal `RESET` before the button enables; the same service call runs
   underneath. Restore-over-existing-data uses the same pattern
   (`--force` / typed confirmation).
8. **New setting (exactly one):** `retention.factory_reset_backup_days = 183`
   — how long factory-reset backups are kept. The daily maintenance retention
   job prunes `backups/factory/` by age using it. Manual backups under
   `backups/manual/` are deliberate operator artifacts and are **never**
   auto-pruned. Documented in `config/waloader.example.toml` +
   `docs/configuration.md` (the doc-sync tests will not pass otherwise).
9. **After a reset, the running UI is stale by design.** The admin page shows
   the report and instructs "restart `serve` now"; the next boot lands on the
   first-run bootstrap screen. No attempt to hot-reinitialize a wiped DB
   under a live Streamlit process.

## 4. Feature specification

### 4.1 Scoped backup service (`services/scoped_backups.py`)

`create_backup(conn, config, scope, *, app_slug=None, include_data=True,
include_logs=False, actor) -> BackupResult(path, manifest)`

| Scope | Contents |
|---|---|
| `all` | DB snapshot + every app's versions/bundles/manifests + datasets + user_files + deleted-app `archives/` + generated caddy config + uploads; logs only with `include_logs=True`; never venvs/uv-cache/tmp |
| `db` | DB snapshot only ("admin data": users, app metadata, settings, app users, dataset metadata, audit) |
| `apps` | every non-deleted app via the shared app-archive builder; `include_data=False` → code only (versions/bundles/manifests), `True` → plus datasets + user_files |
| `app` | one app (requires `app_slug`), same `include_data` switch |

Also: `list_backups(config) -> [BackupInfo]` (manual + factory, newest first,
scope/size/created/purge-date-if-factory) and `delete_backup(config, name)`
(explicit operator action, confirmation in UI).

### 4.2 Restore (`services/restore.py`)

`restore_all(config, archive_path, *, force=False) -> RestoreReport`

- Only `scope=all` archives (manifest-checked; clear error otherwise, incl.
  unsupported `archive_format`).
- Refuses when `waloader.db` exists unless `force` (which wipes per §3.2
  first). Documented precondition: stop `serve` first.
- Restores the DB snapshot and the data tree; ends with a per-app notice:
  "state set to `stopped`; venv missing → rebuild required" (restore
  normalizes every previously-running app to `stopped` and clears stale
  pids/ports validity via the existing reconcile on next boot).
- Round-trip is the acceptance test: backup → wipe → restore → rebuild →
  the app serves again (§7).

### 4.3 App export / import (child-app migration)

- `export_app(conn, config, app, *, include_data, dest_dir) -> Path` — thin
  wrapper over the shared builder; owners may export their own apps, admins
  any.
- `import_app(conn, config, archive_path, *, owner_username=None,
  new_name=None, deploy=True, actor) -> (App, DeployResult|None)`:
  - Validates `archive_format`; name defaults to the archived name,
    `new_name` overrides; availability/slug rules exactly as app creation.
  - Owner: local user matching the archived owner's username, else
    `owner_username` is required (must exist) — clear errors both ways.
  - Recreates versions (byte-exact bundles + sources + manifests), dataset
    concepts + current files, app users + attachments, user_mgmt flag.
    Deployment history is not imported.
  - `deploy=True` (default): run `rebuild_app` on the current version —
    the app ends `running` (or `deployment_failed` with the normal copyable
    error + retry story). `deploy=False`: app ends `stopped` with the
    rebuild-required notice.
  - Works on soft-delete archives (format 2) — documented as un-delete.
- Import is admin-only (it creates users/apps wholesale).

### 4.4 Factory reset (`services/factory_reset.py`)

Per §3.6–§3.9. Report fields: backup path + size + purge_after (or "SKIPPED
— --skip-backup"), apps stopped, caddy stopped, paths removed, leftovers
(locked files, if any — Windows note), "next: restart serve, first-run setup".

### 4.5 CLI — `waloader.tools.backupctl` (new) and `appctl` additions

```bash
uv run python -m waloader.tools.backupctl create --scope all|db|apps [--code-only] [--with-logs]
uv run python -m waloader.tools.backupctl create --scope app --app <slug> [--code-only]
uv run python -m waloader.tools.backupctl list
uv run python -m waloader.tools.backupctl restore <archive.zip> [--force]
uv run python -m waloader.tools.backupctl factory-reset [--skip-backup] [--force]

uv run python -m waloader.tools.appctl export <slug> [--code-only] [--out <dir>]
uv run python -m waloader.tools.appctl import <archive.zip> [--owner <user>] [--name <new name>] [--no-deploy]
uv run python -m waloader.tools.appctl rebuild <slug> | --all
```

`--code-only` is the CLI spelling of `include_data=False`. Every command
prints the produced path / a copyable failure. Exit codes as elsewhere.

### 4.6 UI (thin clients of the above)

- **Admin → Backups & reset (new page):**
  - Create backup: scope radio (all / database only / all apps / one app +
    app selector), with-data toggle (apps scopes), with-logs toggle (all),
    create button with spinner → success shows path + `st.download_button`.
  - Existing backups table (from `list_backups`): name, scope, size,
    created, purge date (factory), download + delete (confirmed).
  - Import app: zip uploader + owner selector + optional new name +
    deploy toggle → runs import, reuses the standard deploy-outcome panel.
  - **Danger zone — Factory reset:** red-bordered section; text input must
    equal `RESET` to enable the button; runs the service with backup on
    (no skip-backup in the UI — CLI only); shows the report and the
    "restart serve" instruction.
- **Gear dialog additions:** "Export app" (download, with/without data) for
  the app's manager; "Rebuild" button shown when the current version's venv
  is missing (import/restore aftermath), calling `rebuild_app` with the
  standard outcome panel.

### 4.7 Migration documentation (WALoader itself)

`docs/backups-and-restore.md` includes the **move-to-a-new-machine runbook**:
`backupctl create --scope all --with-logs` → copy zip + your
`config/waloader.toml` → new machine: repo + `uv sync` + `doctor` →
`backupctl restore <zip>` → `appctl rebuild --all` → `serve`. This, plus the
schema-migration framework that already exists, closes the "migration for
WALoader" story; export/import closes it for individual child apps.

## 5. Build order (phases; each ends tests-green + ruff + PROGRESS/DEVLOG + commit)

- **Q0 Archive foundation:** `app_archive.py` (format 2, enriched metadata),
  deletion switched to it; `scoped_backups.py` (all four scopes, list,
  delete); manifest content tests, zip-content tests (venv exclusion, logs
  opt-in, DB snapshot consistency).
- **Q1 Restore & rebuild:** `restore.py`; `rebuild_app` in the deployment
  module (kind="rebuild"); `lifecycle.start` venv-missing message; `appctl
  rebuild <slug>|--all`. Unit tests + e2e: backup → wipe → restore →
  rebuild → HTTP-healthy (extends the existing e2e module).
- **Q2 Export/import:** services + `appctl export/import`; unit tests
  (owner/name mapping, slug collision, users/datasets fidelity, `--no-deploy`
  state) + e2e import-deploys-and-serves + soft-delete-archive un-delete
  test.
- **Q3 Factory reset + backupctl:** `factory_reset.py`; `backupctl` (all
  subcommands, typed-RESET prompt with `--force` bypass); new retention
  setting + factory-archive pruning wired into maintenance; config docs
  updated (doc-sync green). Tests: wipe-except-backups, confirmation
  refusal paths, skip-backup, retention pruning, restore --force.
- **Q4 UI:** Backups & reset admin page; gear export/rebuild. AppTest
  coverage: scope radio → service call, reset button disabled until typed
  RESET, import validation errors, rebuild visibility.
- **Q5 Docs & final verification:** `docs/backups-and-restore.md`; updates to
  README (CLI table, docs index), configuration.md, troubleshooting
  (cross-links), manual-smoke-checklist (backup/download, import, factory
  reset **against a scratch data dir**); full suites + doctor on this
  machine; DEVLOG/PROGRESS closure; tag `v0.2.0`.

## 6. Testing requirements (minimum)

Archive builder (format 2 fields, venv exclusion, byte-exact bundles);
every scope's zip inventory incl. code-only vs with-data and with-logs;
list/delete; restore (refusal without --force, full round-trip fidelity:
users/apps/datasets/app-users counts equal, states normalized to stopped);
rebuild (venv recreated, app healthy; start-without-venv message); export/
import (fidelity incl. argon2 hashes verifiable after import, slug
collision, owner mapping errors, no-deploy, soft-delete un-delete); factory
reset (backup exists in `backups/factory/` with purge stamp, everything else
wiped, `backups/` preserved, children+caddy stopped, confirmation refusals,
skip-backup, audit row present in the backed-up DB); retention pruning of
factory archives honors the new setting; CLI exit codes + output for every
subcommand; AppTest for the new UI. e2e markers reuse the existing
`e2e`/`caddy` conventions and the warm-uv-cache trick.

## 7. Verification on this machine

```bash
uv run ruff check .
uv run pytest
uv run pytest -m integration
uv run pytest -m e2e          # now incl. restore→rebuild and import round-trips
uv run pytest -m caddy
uv run python -m waloader.tools.doctor
```

Plus the updated manual-smoke items (factory reset walked against a scratch
`WALOADER_CONFIG` data dir, never your real one — the checklist says so).

## 8. Definition of Done

1. §4 fully implemented, service-first, CLI + UI as thin clients; every §6
   behavior has a passing test.
2. All suites green on this machine (unit / integration / e2e / caddy);
   ruff clean; doctor passes.
3. Factory reset demonstrably: backs up first (183-day default retention via
   the new documented setting), requires typed confirmation on both CLI and
   UI, wipes everything except `backups/`, and lands the next boot on
   first-run setup.
4. Backup scopes all/db/apps/app × with-data/code-only produce correct,
   self-describing archives; restore + rebuild round-trip proven end-to-end;
   export/import migrates an app (and un-deletes archived ones).
5. Docs complete (`backups-and-restore.md` + all cross-updates incl. the
   machine-migration runbook); doc-sync tests green; no undocumented
   settings.
6. PROGRESS.md rewritten for G02 and fully checked; DEVLOG appended per
   phase; tagged `v0.2.0`.

## Appendix — wipe semantics (exact)

Wipe (used by factory reset and `restore --force`) removes, under
`data_dir`: `waloader.db` (+ `-wal`/`-shm`), `apps/`, `logs/`, `caddy/`,
`uploads/`, `tmp/`, `uv-cache/`, `archives/` — everything except
`backups/**`. Deletion failures (e.g. Windows file locks) are collected and
reported, never silently ignored; the operation stops all child processes
and Caddy *before* touching files.
