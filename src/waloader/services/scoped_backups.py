"""Scoped manual backups (G02 §4.1).

Scopes: ``all`` (full instance), ``db`` (platform database = admin data),
``apps`` (every app), ``app`` (one app, via the shared format-2 builder so
the result is importable). Archives are zips under ``backups/manual/`` or
``backups/factory/`` — the one subtree wipes always preserve — and the
filesystem is the registry: listing never touches the DB.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import waloader
from waloader.config import WALoaderConfig
from waloader.paths import ensure_dir
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.services import app_archive
from waloader.util import parse_iso, utc_now, utc_now_iso

log = logging.getLogger(__name__)

SCOPES = ("all", "db", "apps", "app")
# top-level data_dir entries never nested inside a backup:
ALWAYS_EXCLUDED_TOP = {"backups", "tmp", "uv-cache"}
DB_FILENAMES_EXCLUDED = {"waloader.db", "waloader.db-wal", "waloader.db-shm"}


class BackupError(Exception):
    pass


@dataclass
class BackupResult:
    path: Path
    manifest: dict


@dataclass
class BackupInfo:
    name: str
    path: Path
    kind: str  # manual | factory
    scope: str
    size_bytes: int
    created_at: str
    purge_after: str | None = None


def manual_dir(config: WALoaderConfig) -> Path:
    return config.backups_dir / "manual"


def factory_dir(config: WALoaderConfig) -> Path:
    return config.backups_dir / "factory"


def _db_snapshot_into(config: WALoaderConfig, archive: zipfile.ZipFile) -> None:
    """Consistent DB snapshot (sqlite backup API) written as 'waloader.db'."""
    if not config.database_path.exists():
        raise BackupError(f"database does not exist yet: {config.database_path}")
    temp = config.database_path.with_suffix(".backup-snapshot.tmp")
    source = sqlite3.connect(config.database_path)
    try:
        dest = sqlite3.connect(temp)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    try:
        archive.write(temp, "waloader.db")
    finally:
        temp.unlink(missing_ok=True)


def _walk_data_tree(config: WALoaderConfig, *, include_logs: bool):
    """(absolute_path, zip_name) for the all-scope tree (minus DB + exclusions)."""
    data_dir = config.data_dir
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(data_dir)
        top = relative.parts[0]
        if top in ALWAYS_EXCLUDED_TOP:
            continue
        if top == "logs" and not include_logs:
            continue
        if len(relative.parts) == 1 and relative.name in DB_FILENAMES_EXCLUDED:
            continue  # the consistent snapshot replaces the raw DB files
        if top == "apps" and len(relative.parts) >= 3 and relative.parts[2] == "runtime":
            continue  # venvs are rebuildable, never archived
        yield path, relative.as_posix()


def _base_manifest(config: WALoaderConfig, scope: str, **extra) -> dict:
    return {
        "archive_format": app_archive.ARCHIVE_FORMAT,
        "kind": "backup",
        "scope": scope,
        "created_at": utc_now_iso(),
        "waloader_version": waloader.__version__,
        "effective_config": config.model_dump(),  # paths/flags only; no secret values
        **extra,
    }


def _apps_inventory(conn: sqlite3.Connection) -> list[dict]:
    return [
        {"slug": a.slug, "name": a.name, "state": a.state,
         "current_version": a.current_version}
        for a in apps_repo.list_all(conn, include_deleted=True)
    ]


def _target_path(dest_dir: Path, scope_label: str) -> Path:
    ensure_dir(dest_dir)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    target = dest_dir / f"{scope_label}-{stamp}.zip"
    counter = 1
    while target.exists():
        target = dest_dir / f"{scope_label}-{stamp}-{counter}.zip"
        counter += 1
    return target


def create_backup(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    scope: str,
    *,
    app_slug: str | None = None,
    include_data: bool = True,
    include_logs: bool = False,
    actor: str = "",
    dest_dir: Path | None = None,
    scope_label: str | None = None,
) -> BackupResult:
    if scope not in SCOPES:
        raise BackupError(f"Unknown backup scope {scope!r}; one of {SCOPES}")
    destination = dest_dir or manual_dir(config)

    if scope == "app":
        if not app_slug:
            raise BackupError("scope 'app' needs an app slug")
        app = apps_repo.get_by_slug(conn, app_slug)
        if app is None:
            raise BackupError(f"No app with slug '{app_slug}'")
        path = app_archive.build_app_archive(
            conn, config, app, include_data=include_data,
            dest_dir=destination, stem=f"app-{app.slug}",
        )
        manifest = app_archive.read_metadata(path)
    else:
        path = _target_path(destination, scope_label or scope)
        if scope == "db":
            manifest = _base_manifest(config, "db")
        elif scope == "apps":
            manifest = _base_manifest(
                config, "apps", include_data=include_data,
                apps=_apps_inventory(conn),
            )
        else:  # all
            manifest = _base_manifest(
                config, "all", include_data=True, include_logs=include_logs,
                apps=_apps_inventory(conn),
            )
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
                if scope in ("db", "all"):
                    _db_snapshot_into(config, archive)
                if scope == "all":
                    for file_path, zip_name in _walk_data_tree(
                        config, include_logs=include_logs
                    ):
                        archive.write(file_path, zip_name)
                if scope == "apps":
                    for app in apps_repo.list_all(conn, include_deleted=True):
                        metadata = app_archive.build_app_metadata(
                            conn, config, app, include_data=include_data
                        )
                        archive.writestr(
                            f"apps/{app.slug}/metadata.json",
                            json.dumps(metadata, indent=2),
                        )
                        for file_path, zip_name in app_archive._iter_app_files(
                            config, app, include_data=include_data
                        ):
                            archive.write(file_path, f"apps/{zip_name}")
        except BackupError:
            path.unlink(missing_ok=True)
            raise

    audit_repo.record(
        conn, actor=actor, action="backup.create",
        target=path.name, details={"scope": scope, "include_data": include_data},
    )
    conn.commit()
    log.info("backup created: %s (scope=%s)", path, scope)
    return BackupResult(path=path, manifest=manifest)


# --- registry (filesystem only — a factory reset wipes the DB) --------------


def _info_for(config: WALoaderConfig, path: Path, kind: str) -> BackupInfo:
    stat = path.stat()
    scope = "unknown"
    created = utc_now_iso()
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            member = "manifest.json" if "manifest.json" in names else "metadata.json"
            manifest = json.loads(archive.read(member))
            scope = manifest.get("scope") or manifest.get("kind", "unknown")
            created = manifest.get("created_at", created)
    except Exception:  # corrupt/foreign zip: still listed, marked unknown
        pass
    purge_after = None
    if kind == "factory":
        purge_after = (
            parse_iso(created)
            + timedelta(days=config.retention.factory_reset_backup_days)
        ).isoformat()
    return BackupInfo(
        name=path.name, path=path, kind=kind, scope=scope,
        size_bytes=stat.st_size, created_at=created, purge_after=purge_after,
    )


def list_backups(config: WALoaderConfig) -> list[BackupInfo]:
    infos: list[BackupInfo] = []
    for kind, directory in (("manual", manual_dir(config)), ("factory", factory_dir(config))):
        if not directory.exists():
            continue
        for path in directory.glob("*.zip"):
            infos.append(_info_for(config, path, kind))
    infos.sort(key=lambda i: i.created_at, reverse=True)
    return infos


def delete_backup(config: WALoaderConfig, name: str) -> None:
    """Explicit operator deletion; `name` is a bare filename, never a path."""
    if Path(name).name != name or not name.endswith(".zip"):
        raise BackupError(f"Invalid backup name: {name!r}")
    for directory in (manual_dir(config), factory_dir(config)):
        candidate = directory / name
        if candidate.exists():
            candidate.unlink()
            log.info("backup deleted: %s", candidate)
            return
    raise BackupError(f"No backup named {name!r}")


def cleanup_factory_backups(config: WALoaderConfig) -> list[Path]:
    """Prune factory-reset backups past retention (manual ones never expire)."""
    import time

    directory = factory_dir(config)
    if not directory.exists():
        return []
    cutoff = time.time() - config.retention.factory_reset_backup_days * 86400
    removed = []
    for path in sorted(directory.glob("*.zip")):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    if removed:
        log.info("removed %d expired factory backup(s)", len(removed))
    return removed
