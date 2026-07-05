"""App deletion: soft delete -> compressed archive -> retention -> hard delete.

Archives use the shared format-2 builder (services/app_archive.py), so a
soft-delete archive is importable — deletion is reversible until retention
expires (`appctl import <archive>` un-deletes).
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.services import caddy, layout, processes, states
from waloader.services.app_archive import build_app_archive
from waloader.util import utc_now, utc_now_iso

log = logging.getLogger(__name__)


def soft_delete_app(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, *, actor: str = ""
) -> Path:
    """Stop, archive, mark deleted (pending hard delete), hide, free disk."""
    processes.stop_app(conn, config, app)
    app = states.transition(conn, app, states.PENDING_DELETE)
    archive_path = build_app_archive(
        conn, config, app, include_data=True, dest_dir=config.archives_dir
    )

    purge_after = (
        utc_now() + timedelta(days=config.retention.deleted_app_days)
    ).replace(microsecond=0).isoformat()
    apps_repo.mark_deleted(
        conn, app.id,
        archive_path=layout.relativize(config, archive_path),
        purge_after=purge_after,
    )
    shutil.rmtree(layout.app_dir(config, app.slug), ignore_errors=True)
    if config.caddy.enabled:
        caddy.refresh_routes(conn, config)  # drop the app's route
    audit_repo.record(conn, actor=actor, action="app.soft_delete", target=app.slug,
                      details={"archive": archive_path.name, "purge_after": purge_after})
    conn.commit()
    log.info("app %s soft-deleted; archive %s; purge after %s",
             app.slug, archive_path.name, purge_after)
    return archive_path


def hard_delete_expired(
    conn: sqlite3.Connection, config: WALoaderConfig, *, now_iso: str | None = None
) -> list[str]:
    """Purge apps whose retention expired: archive file, logs, row (port frees)."""
    purged = []
    for app in apps_repo.list_purge_due(conn, now_iso or utc_now_iso()):
        if app.archive_path:
            layout.resolve(config, app.archive_path).unlink(missing_ok=True)
        shutil.rmtree(config.logs_dir / "apps" / app.slug, ignore_errors=True)
        apps_repo.hard_delete(conn, app.id)
        audit_repo.record(conn, actor="maintenance", action="app.hard_delete",
                          target=app.slug)
        purged.append(app.slug)
    conn.commit()
    if purged:
        log.info("hard-deleted expired app(s): %s", ", ".join(purged))
    return purged
