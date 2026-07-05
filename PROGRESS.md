# PROGRESS.md — WALoader build status

**Active goal:** `goals/G02-backups-reset-migration.md` — **COMPLETE** (v0.2.0;
G01 complete at v0.1.0)
**Current phase:** all G02 phases done (Q0–Q5)
**Last validation:** 2026-07-05 G02 final: unit 367 · integration 2 · e2e 4
(incl. backup→wipe→restore→rebuild→serving and export→delete→import→serving
round trips) · caddy 3 · ruff clean · doctor all checks passed

## G02 phase checklist

- [x] **Q0 Archive foundation** — format-2 app archives (owner username,
      versions, dataset rows, app users + attachments; venvs never archived),
      deletion on the shared builder (soft-deletes importable), scoped backup
      service (all/db/apps/app × with-data/code-only × with-logs), filesystem
      registry (list/delete/factory pruning), retention.factory_reset_backup_days
- [x] **Q1 Restore & rebuild** — restore_all (all-scope validation, --force
      wipe-except-backups, zip-slip guard, states normalized), rebuild_app via
      the pipeline (kind="rebuild"), lifecycle.start venv-missing refusal,
      appctl rebuild <slug>|--all, real e2e round trip
- [x] **Q2 Export/import** — export to backups/manual, import with owner/name
      mapping + full fidelity (argon2 hashes authenticate after import),
      soft-delete un-delete, scope-backup redirect, appctl export/import
- [x] **Q3 Factory reset + backupctl** — stop-all → audited full backup (with
      logs) into backups/factory/ → wipe except backups/ → report with undo
      steps; backupctl create/list/restore/factory-reset (typed RESET, --force,
      --skip-backup); maintenance prunes expired factory backups
- [x] **Q4 UI** — Admin "Backups & reset" page (create/download/list/delete,
      import app, danger zone with typed RESET + server-side gate, post-reset
      render safety), gear export + rebuild, card rebuild indicator
- [x] **Q5 Docs & final verification** — docs/backups-and-restore.md (scopes,
      restore, rebuild, export/import, factory reset, machine-migration
      runbook, retention table), README/troubleshooting/smoke-checklist
      updates, all suites + doctor green, tag v0.2.0

## Definition of Done (G02 §8) — final status

1. ✅ §4 implemented service-first; CLI + UI thin clients; §6 behaviors tested
   (382 tests total across suites)
2. ✅ unit 367 · integration 2 · e2e 4 · caddy 3 — green on this machine;
   ruff clean; doctor passes
3. ✅ Factory reset backs up first (183-day configurable retention via the
   documented setting), typed confirmation on CLI and UI (server-side
   re-check), wipes everything except `backups/`, next boot = first-run setup
4. ✅ All backup scopes correct and self-describing; restore + rebuild proven
   end-to-end; export/import migrates apps and un-deletes archives
5. ✅ docs/backups-and-restore.md + configuration/README/troubleshooting/
   checklist updates; doc-sync tests green; no undocumented settings
6. ✅ PROGRESS/DEVLOG maintained per phase; tagged v0.2.0

## Known limitations (accepted, documented)

- Restored/imported apps need `appctl rebuild` (venvs are never archived —
  by design; the UI/CLI point at the fix everywhere it can surface)
- `db`-scope backups restore manually (documented); only `all`-scope archives
  go through `backupctl restore`
- Old (pre-G02) soft-delete archives lack format-2 metadata and cannot be
  imported; new ones can
- Plus the G01 limitations (see docs/troubleshooting.md)

## Known blockers

None.
