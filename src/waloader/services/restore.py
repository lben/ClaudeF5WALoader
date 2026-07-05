"""Full-instance restore from an all-scope backup, and the shared wipe.

``wipe_data_dir`` is the one implementation of "delete everything under
data_dir except backups/" used by both ``restore --force`` and factory reset.
Restore extracts an all-scope archive back into place, then normalizes app
states: processes recorded in the archived DB are long gone, and venvs are
never archived — every app comes back ``stopped`` and needs a rebuild.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from waloader import db
from waloader.config import WALoaderConfig
from waloader.paths import ensure_dir
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import app_archive, layout, uv_env

log = logging.getLogger(__name__)

PRESERVED_TOP_DIRS = ("backups",)


class RestoreError(Exception):
    pass


@dataclass
class WipeReport:
    removed: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)  # locked files etc.


def wipe_data_dir(config: WALoaderConfig) -> WipeReport:
    """Remove everything under data_dir except backups/. Never silent about
    failures (Windows file locks land in ``leftovers``)."""
    report = WipeReport()
    data_dir = config.data_dir
    if not data_dir.exists():
        return report
    for entry in sorted(data_dir.iterdir()):
        if entry.name in PRESERVED_TOP_DIRS:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            report.removed.append(entry.name)
        except OSError as exc:
            log.warning("wipe: could not remove %s: %s", entry, exc)
            report.leftovers.append(f"{entry.name}: {exc}")
    return report


@dataclass
class RestoreReport:
    archive: str
    files_restored: int
    apps: int
    rebuild_required: list[str]
    wipe: WipeReport | None
    notes: list[str] = field(default_factory=list)


def _read_backup_manifest(archive_path: Path) -> dict:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, OSError, json.JSONDecodeError) as exc:
        raise RestoreError(
            f"Not a readable WALoader backup: {archive_path} ({exc})"
        ) from exc
    if manifest.get("archive_format") != app_archive.ARCHIVE_FORMAT:
        raise RestoreError(
            f"Unsupported archive_format {manifest.get('archive_format')!r} "
            f"(this WALoader reads format {app_archive.ARCHIVE_FORMAT})"
        )
    if manifest.get("scope") != "all":
        raise RestoreError(
            f"Full restore needs an all-scope backup; this archive has scope "
            f"{manifest.get('scope')!r}. Per-app archives are imported with "
            "'appctl import' instead."
        )
    return manifest


def restore_all(
    config: WALoaderConfig, archive_path: Path, *, force: bool = False
) -> RestoreReport:
    """Restore a full instance. Precondition: WALoader (serve) is not running."""
    manifest = _read_backup_manifest(archive_path)

    wipe_report: WipeReport | None = None
    if config.database_path.exists():
        if not force:
            raise RestoreError(
                f"A database already exists at {config.database_path}. Stop "
                "serve, then re-run with --force to replace the current data "
                "directory (backups/ is preserved)."
            )
        wipe_report = wipe_data_dir(config)

    data_dir = ensure_dir(config.data_dir)
    resolved_root = data_dir.resolve()
    files_restored = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir() or member.filename == "manifest.json":
                continue
            if member.filename == "waloader.db":
                target = config.database_path
            else:
                target = data_dir / member.filename
            resolved = target.resolve()
            if resolved != resolved_root and resolved_root not in resolved.parents:
                raise RestoreError(
                    f"Archive member escapes the data directory: {member.filename!r}"
                )
            ensure_dir(resolved.parent)
            with archive.open(member) as src, open(resolved, "wb") as dst:
                shutil.copyfileobj(src, dst)
            files_restored += 1

    # normalize: archived pids are dead, venvs were never archived
    conn = db.connect(config.database_path)
    try:
        db.migrate(conn)  # the archive may predate newer migrations
        rebuild_required: list[str] = []
        apps = apps_repo.list_all(conn)
        for app in apps:
            if app.state in ("running", "deploying"):
                apps_repo.set_state(conn, app.id, "stopped")
            runtime_repo.clear_process(conn, app.id)
            if app.current_version is not None:
                venv_python = uv_env.venv_python(
                    layout.venv_dir(config, app.slug, app.current_version)
                )
                if not venv_python.exists():
                    rebuild_required.append(app.slug)
        audit_repo.record(
            conn, actor="restore", action="restore.all", target=archive_path.name,
            details={"files": files_restored, "rebuild_required": rebuild_required},
        )
        conn.commit()
    finally:
        conn.close()

    notes = [
        "All previously-running apps are now 'stopped'.",
    ]
    if rebuild_required:
        notes.append(
            "Venvs are never archived — rebuild before starting: "
            f"appctl rebuild --all (needed: {', '.join(rebuild_required)})"
        )
    log.info("restore complete: %s (%d files, %d apps)",
             archive_path.name, files_restored, len(apps))
    return RestoreReport(
        archive=archive_path.name,
        files_restored=files_restored,
        apps=len(apps),
        rebuild_required=rebuild_required,
        wipe=wipe_report,
        notes=notes,
    )
