"""Maintenance orchestration: the daily jobs, runnable on demand (no cron)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from waloader.config import WALoaderConfig
from waloader.services import backups, deletion


@dataclass
class MaintenanceReport:
    backup_created: bool = False
    backup_reason: str = ""
    backups_removed: int = 0
    logs_removed: int = 0
    apps_purged: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"backup: {'created' if self.backup_created else self.backup_reason}; "
            f"expired backups removed: {self.backups_removed}; "
            f"old log files removed: {self.logs_removed}; "
            f"apps hard-deleted: {len(self.apps_purged)}"
        )


def run_all(conn: sqlite3.Connection, config: WALoaderConfig) -> MaintenanceReport:
    report = MaintenanceReport()
    backup = backups.backup_database(config)
    report.backup_created = backup.created
    report.backup_reason = backup.reason
    report.backups_removed = len(backups.cleanup_backups(config))
    report.logs_removed = backups.cleanup_logs(config)
    report.apps_purged = deletion.hard_delete_expired(conn, config)
    return report
