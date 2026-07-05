"""Backup CLI: create | list | restore | factory-reset.

Unlike other tools this one must work when the database is absent, missing
(restore into a fresh data dir) or about to be destroyed (factory reset) — so
it loads config directly and never auto-creates/migrates the DB.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from waloader import db
from waloader.config import ConfigError, WALoaderConfig, apply_db_overrides, load_config
from waloader.repositories import settings as settings_repo
from waloader.services import factory_reset as factory_reset_service
from waloader.services import restore as restore_service
from waloader.services import scoped_backups as sb


def _config() -> WALoaderConfig:
    try:
        loaded = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if loaded.config.database_path.exists():
        conn = db.connect(loaded.config.database_path)
        try:
            loaded = apply_db_overrides(loaded, settings_repo.get_all(conn))
        except Exception:
            pass  # unreadable overrides never block backup/restore/reset
        finally:
            conn.close()
    return loaded.config


def _confirm_reset(force: bool) -> bool:
    if force:
        return True
    print("This DESTROYS the WALoader data directory (a full backup is taken "
          "first unless --skip-backup). backups/ is preserved.")
    try:
        answer = input("Type RESET to proceed: ")
    except EOFError:
        print("No interactive terminal — use --force for scripted resets.",
              file=sys.stderr)
        return False
    if answer.strip() != "RESET":
        print("Aborted (confirmation did not match).", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.backupctl",
        description="Scoped backups, restore, and factory reset",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create a scoped backup zip")
    create.add_argument("--scope", required=True, choices=list(sb.SCOPES))
    create.add_argument("--app", help="app slug (required for --scope app)")
    create.add_argument("--code-only", action="store_true",
                        help="apps scopes: exclude datasets and user files")
    create.add_argument("--with-logs", action="store_true",
                        help="all scope: include the logs tree")

    sub.add_parser("list", help="list manual and factory backups")

    restore_cmd = sub.add_parser("restore", help="restore a full (all-scope) backup")
    restore_cmd.add_argument("archive")
    restore_cmd.add_argument("--force", action="store_true",
                             help="replace the existing data dir (backups/ kept)")

    reset = sub.add_parser("factory-reset",
                           help="backup everything, then wipe back to first-run")
    reset.add_argument("--skip-backup", action="store_true",
                       help="DANGEROUS: reset without the safety backup")
    reset.add_argument("--force", action="store_true",
                       help="skip the typed-RESET confirmation (scripts)")

    args = parser.parse_args(argv)
    config = _config()

    if args.command == "create":
        if not config.database_path.exists():
            print("No database — nothing to back up yet.", file=sys.stderr)
            return 1
        conn = db.connect(config.database_path)
        try:
            try:
                result = sb.create_backup(
                    conn, config, args.scope, app_slug=args.app,
                    include_data=not args.code_only,
                    include_logs=args.with_logs, actor="backupctl",
                )
            except sb.BackupError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        finally:
            conn.close()
        print(f"backup created: {result.path}")
        return 0

    if args.command == "list":
        infos = sb.list_backups(config)
        if not infos:
            print("no backups")
            return 0
        print(f"{'NAME':44} {'KIND':8} {'SCOPE':8} {'SIZE':>10}  CREATED / PURGE")
        for info in infos:
            purge = f" -> purge {info.purge_after}" if info.purge_after else ""
            print(f"{info.name:44} {info.kind:8} {info.scope:8} "
                  f"{info.size_bytes:>10,}  {info.created_at}{purge}")
        return 0

    if args.command == "restore":
        try:
            report = restore_service.restore_all(
                config, Path(args.archive), force=args.force
            )
        except restore_service.RestoreError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"restored {report.files_restored} file(s), {report.apps} app(s) "
              f"from {report.archive}")
        for note in report.notes:
            print(f"  {note}")
        return 0

    if args.command == "factory-reset":
        if not _confirm_reset(args.force):
            return 1
        report = factory_reset_service.factory_reset(
            config, skip_backup=args.skip_backup, actor="backupctl"
        )
        print(report.summary())
        for note in report.notes:
            print(f"  {note}")
        return 1 if report.wipe.leftovers else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
