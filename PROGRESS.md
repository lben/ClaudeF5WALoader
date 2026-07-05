# PROGRESS.md — WALoader build status

**Active goal:** `goals/G02-backups-reset-migration.md` (G01 complete, tagged v0.1.0)
**Current phase:** Q4 — UI (in progress)
**Last validation:** G01 final (2026-07-03): unit 315 · integration 2 · e2e 1 · caddy 3 · ruff clean · doctor pass

## G02 phase checklist

- [x] **Q0 Archive foundation** — services/app_archive.py (archive_format 2 with
      enriched metadata: owner username, versions, dataset rows, app users,
      attachments), deletion switched to the shared builder,
      services/scoped_backups.py (scopes all/db/apps/app, code-only/with-data,
      with-logs, list, delete), zip-content + manifest tests
- [x] **Q1 Restore & rebuild** — services/restore.py (all-scope archives, --force
      wipe-except-backups, states normalized to stopped), rebuild_app via the
      deployment pipeline (kind="rebuild"), lifecycle.start venv-missing message,
      appctl rebuild <slug>|--all, e2e backup→wipe→restore→rebuild→healthy
- [x] **Q2 Export/import** — export_app/import_app services (owner/name mapping,
      slug availability, users/datasets fidelity, deploy-by-default),
      appctl export/import, soft-delete un-delete test, e2e import round-trip
- [x] **Q3 Factory reset + backupctl** — services/factory_reset.py (stop all →
      full backup into backups/factory/ → wipe except backups/ → report),
      backupctl CLI (create/list/restore/factory-reset, typed-RESET confirmation,
      --force, --skip-backup), retention.factory_reset_backup_days setting +
      maintenance pruning, config docs updated
- [ ] **Q4 UI** — Admin "Backups & reset" page (create/download/list/delete,
      import app, danger-zone reset with typed RESET), gear dialog export +
      rebuild-when-venv-missing, AppTest coverage
- [ ] **Q5 Docs & final verification** — docs/backups-and-restore.md (incl.
      machine-migration runbook), README/configuration/troubleshooting/
      smoke-checklist updates, all suites + doctor green, tag v0.2.0

## Active acceptance criteria (Q0)

- Format-2 archives carry everything import needs; venvs never archived
- Soft-delete archives produced by the shared builder (importable going forward)
- All four backup scopes produce correct, self-describing zips; DB snapshot via
  sqlite backup API; backups/, tmp/, uv-cache/ never nested inside a backup
- `uv run pytest` and `uv run ruff check .` green

## Known blockers

None.
