"""Factory reset (G02 §4.4): stop everything → full safety backup → wipe.

The backup (scope *all*, logs included) lands in ``backups/factory/`` — the
subtree the wipe preserves — and is pruned by daily maintenance after
``retention.factory_reset_backup_days`` (default 183 ≈ 6 months). The service
performs no confirmation itself: its clients (backupctl with typed RESET or
--force; the admin UI danger zone) own that gate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from waloader import db
from waloader.config import WALoaderConfig
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.services import caddy, processes, restore
from waloader.services import scoped_backups as sb
from waloader.util import utc_now

log = logging.getLogger(__name__)


@dataclass
class FactoryResetReport:
    backup_path: str | None
    backup_skipped: bool
    purge_after: str | None
    apps_stopped: list[str]
    caddy_stopped: bool
    wipe: restore.WipeReport
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        backup = (
            "backup SKIPPED (--skip-backup)" if self.backup_skipped
            else f"backup: {self.backup_path} (kept until {self.purge_after})"
        )
        leftovers = (
            f"; could NOT remove: {', '.join(self.wipe.leftovers)}"
            if self.wipe.leftovers else ""
        )
        return (
            f"{backup}; apps stopped: {len(self.apps_stopped)}; "
            f"removed: {', '.join(self.wipe.removed) or 'nothing'}{leftovers}"
        )


def factory_reset(
    config: WALoaderConfig, *, skip_backup: bool = False, actor: str = ""
) -> FactoryResetReport:
    """The destructive core. Callers MUST have confirmed already."""
    apps_stopped: list[str] = []
    backup_path: str | None = None
    purge_after: str | None = None
    notes: list[str] = []

    if config.database_path.exists():
        conn = db.connect(config.database_path)
        try:
            for app in apps_repo.list_all(conn, include_deleted=True):
                if processes.stop_app(conn, config, app):
                    apps_stopped.append(app.slug)
            # audit BEFORE the backup so the event is inside the snapshot
            audit_repo.record(
                conn, actor=actor, action="factory_reset.run", target="",
                details={"skip_backup": skip_backup},
            )
            conn.commit()
            if not skip_backup:
                result = sb.create_backup(
                    conn, config, "all", include_logs=True, actor=actor,
                    dest_dir=sb.factory_dir(config), scope_label="factory",
                )
                backup_path = str(result.path)
                purge_after = (
                    utc_now()
                    + timedelta(days=config.retention.factory_reset_backup_days)
                ).replace(microsecond=0).isoformat()
        finally:
            conn.close()
    else:
        notes.append("no database found — nothing to stop or back up; wiping only")
        if not skip_backup:
            skip_backup = True

    caddy_stopped = caddy.stop(config).ok
    wipe = restore.wipe_data_dir(config)

    notes.append(
        "Factory reset complete. Restart `serve` now — the next start lands on "
        "the first-run setup screen."
    )
    if backup_path:
        notes.append(
            "To undo: `python -m waloader.tools.backupctl restore "
            f"{backup_path}` then `appctl rebuild --all`."
        )
    report = FactoryResetReport(
        backup_path=backup_path,
        backup_skipped=skip_backup,
        purge_after=purge_after,
        apps_stopped=apps_stopped,
        caddy_stopped=caddy_stopped,
        wipe=wipe,
        notes=notes,
    )
    log.warning("FACTORY RESET by %s: %s", actor or "unknown", report.summary())
    return report
