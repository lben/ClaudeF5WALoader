"""App deletion: soft delete -> compressed archive -> retention -> hard delete."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import zipfile
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.paths import ensure_dir
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import deployments as deployments_repo
from waloader.repositories import versions as versions_repo
from waloader.services import caddy, layout, processes, states
from waloader.util import utc_now, utc_now_iso

log = logging.getLogger(__name__)

ARCHIVE_EXCLUDED_TOP_DIRS = {"runtime"}  # venvs are rebuildable, never archived


def _archive_app_dir(config: WALoaderConfig, app: App, metadata: dict) -> Path:
    ensure_dir(config.archives_dir)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    archive_path = config.archives_dir / f"{app.slug}-{stamp}.zip"
    app_directory = layout.app_dir(config, app.slug)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, indent=2))
        if app_directory.exists():
            for path in sorted(app_directory.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(app_directory)
                if relative.parts and relative.parts[0] in ARCHIVE_EXCLUDED_TOP_DIRS:
                    continue
                archive.write(path, str(Path(app.slug) / relative))
        # log references, not full logs: record where they live
    return archive_path


def soft_delete_app(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, *, actor: str = ""
) -> Path:
    """Stop, archive, mark deleted (pending hard delete), hide, free disk."""
    processes.stop_app(conn, config, app)
    app = states.transition(conn, app, states.PENDING_DELETE)

    metadata = {
        "app": asdict(app),
        "versions": [asdict(v) for v in versions_repo.list_for_app(conn, app.id)],
        "deployments": [asdict(d) for d in deployments_repo.list_for_app(conn, app.id)],
        "log_dir_reference": f"logs/apps/{app.slug}/",
        "archived_at": utc_now_iso(),
    }
    archive_path = _archive_app_dir(config, app, metadata)

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
