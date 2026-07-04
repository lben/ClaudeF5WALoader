"""Maintenance CLI: backup-db | cleanup-retention | cleanup-logs |
archive-deleted-apps | hard-delete-expired-apps | run-all."""

from __future__ import annotations

import argparse

from waloader.repositories import apps as apps_repo
from waloader.services import backups, deletion, maintenance_service
from waloader.tools._common import bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.maintenance", description="WALoader maintenance jobs"
    )
    parser.add_argument(
        "command",
        choices=[
            "backup-db", "cleanup-retention", "cleanup-logs",
            "archive-deleted-apps", "hard-delete-expired-apps", "run-all",
        ],
    )
    args = parser.parse_args(argv)

    config, conn = bootstrap()
    try:
        if args.command == "backup-db":
            result = backups.backup_database(config)
            print(result.reason + (f": {result.path}" if result.path else ""))
        elif args.command == "cleanup-retention":
            removed = backups.cleanup_backups(config)
            purged = deletion.hard_delete_expired(conn, config)
            print(f"expired backups removed: {len(removed)}; apps purged: {purged or 'none'}")
        elif args.command == "cleanup-logs":
            print(f"old log files removed: {backups.cleanup_logs(config)}")
        elif args.command == "archive-deleted-apps":
            # deletion archives at soft-delete time; this catches stragglers whose
            # archive file is missing (e.g. archives dir was moved/damaged).
            missing = [
                app for app in apps_repo.list_all(conn, include_deleted=True)
                if app.deleted_at is not None and (
                    app.archive_path is None
                    or not (config.data_dir / app.archive_path).exists()
                )
            ]
            if not missing:
                print("all deleted apps have archives")
            for app in missing:
                print(f"warning: '{app.slug}' is deleted but its archive is missing "
                      "(source data was already removed at delete time)")
        elif args.command == "hard-delete-expired-apps":
            purged = deletion.hard_delete_expired(conn, config)
            print(f"apps hard-deleted: {purged or 'none'}")
        elif args.command == "run-all":
            report = maintenance_service.run_all(conn, config)
            print(report.summary())
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
