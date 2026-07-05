"""Shared app archive builder (archive_format 2).

One format, one builder — used by soft-deletion, scoped backups (app scope),
and export. The metadata carries everything import needs to recreate the app
on this or another WALoader instance: the app row (with the owner's
*username*, portable across instances), versions, dataset concepts + current
files, app users (argon2 hashes are portable), and attachments. Venvs
(``runtime/``) are rebuildable by definition and are never archived.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zipfile
from dataclasses import asdict
from pathlib import Path

import waloader
from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.paths import ensure_dir
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import datasets as datasets_repo
from waloader.repositories import deployments as deployments_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo
from waloader.services import layout
from waloader.util import utc_now, utc_now_iso

log = logging.getLogger(__name__)

ARCHIVE_FORMAT = 2
EXCLUDED_TOP_DIRS = {"runtime"}  # rebuildable; never archived


class ArchiveError(Exception):
    """User-facing archive problem (unsupported format, corrupt zip, ...)."""


def build_app_metadata(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, *, include_data: bool
) -> dict:
    try:
        owner_username = users_repo.get(conn, app.owner_id).username
    except KeyError:
        owner_username = ""
    concepts = []
    for concept in datasets_repo.list_concepts(conn, app.id):
        current = datasets_repo.current_file(conn, concept.id)
        concepts.append(
            {
                "name": concept.name,
                "created_at": concept.created_at,
                "current_file": asdict(current) if current else None,
            }
        )
    app_users = []
    for app_user in app_users_repo.list_for_app(conn, app.id):
        app_users.append(
            {
                **asdict(app_user),
                "attachments": [
                    asdict(a) for a in app_users_repo.list_attachments(conn, app_user.id)
                ],
            }
        )
    return {
        "archive_format": ARCHIVE_FORMAT,
        "kind": "app",
        "created_at": utc_now_iso(),
        "waloader_version": waloader.__version__,
        "include_data": include_data,
        "app": {**asdict(app), "owner_username": owner_username},
        "versions": [asdict(v) for v in versions_repo.list_for_app(conn, app.id)],
        "dataset_concepts": concepts,
        "app_users": app_users,
        "deployments": [asdict(d) for d in deployments_repo.list_for_app(conn, app.id)],
        "log_dir_reference": f"logs/apps/{app.slug}/",
    }


def _iter_app_files(config: WALoaderConfig, app: App, *, include_data: bool):
    """(absolute_path, zip_name) pairs for the app's archivable tree."""
    app_directory = layout.app_dir(config, app.slug)
    if not app_directory.exists():
        return
    for path in sorted(app_directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(app_directory)
        top = relative.parts[0] if relative.parts else ""
        if top in EXCLUDED_TOP_DIRS:
            continue
        if not include_data and top in ("datasets", "user_files"):
            continue
        yield path, str(Path(app.slug) / relative)


def build_app_archive(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    *,
    include_data: bool = True,
    dest_dir: Path,
    stem: str | None = None,
) -> Path:
    ensure_dir(dest_dir)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    archive_path = dest_dir / f"{stem or app.slug}-{stamp}.zip"
    counter = 1
    while archive_path.exists():
        archive_path = dest_dir / f"{stem or app.slug}-{stamp}-{counter}.zip"
        counter += 1
    metadata = build_app_metadata(conn, config, app, include_data=include_data)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, indent=2))
        for path, zip_name in _iter_app_files(config, app, include_data=include_data):
            archive.write(path, zip_name)
    log.info("app archive written: %s (include_data=%s)", archive_path, include_data)
    return archive_path


def read_metadata(archive_path: Path) -> dict:
    """Load + sanity-check an archive's metadata for import/inspection."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            raw = archive.read("metadata.json")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ArchiveError(f"Not a readable WALoader archive: {archive_path} ({exc})") from exc
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArchiveError(f"Archive metadata is not valid JSON: {exc}") from exc
    fmt = metadata.get("archive_format")
    if fmt != ARCHIVE_FORMAT:
        raise ArchiveError(
            f"Unsupported archive_format {fmt!r} (this WALoader reads format "
            f"{ARCHIVE_FORMAT}). Older soft-delete archives without metadata "
            "versioning cannot be imported."
        )
    return metadata
