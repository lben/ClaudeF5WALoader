"""App export/import — child-app migration between WALoader instances,
and un-delete for soft-delete archives (same format-2 zips).

Import recreates the app under a fresh slug (availability rules identical to
app creation), rewrites stored paths to the new slug, restores versions with
byte-exact bundles, dataset concepts (+ current files when the archive has
data), app users (argon2 hashes are portable) and attachments — then, by
default, rebuilds via the normal deployment pipeline so the app ends running.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import zipfile
from pathlib import Path, PurePosixPath

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.paths import ensure_dir
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import datasets as datasets_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo
from waloader.services import app_archive, deployment, layout, scoped_backups, slugs

log = logging.getLogger(__name__)


class ImportAppError(Exception):
    """User-facing import problem; message says how to fix it."""


def export_app(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    *,
    include_data: bool = True,
    dest_dir: Path | None = None,
    actor: str = "",
) -> Path:
    path = app_archive.build_app_archive(
        conn, config, app, include_data=include_data,
        dest_dir=dest_dir or scoped_backups.manual_dir(config),
        stem=f"app-{app.slug}",
    )
    audit_repo.record(conn, actor=actor, action="app.export", target=app.slug,
                      details={"archive": path.name, "include_data": include_data})
    conn.commit()
    return path


def _resolve_owner(conn: sqlite3.Connection, metadata: dict, owner_username: str | None):
    if owner_username:
        owner = users_repo.get_by_username(conn, owner_username)
        if owner is None:
            raise ImportAppError(f"No local user named '{owner_username}'")
        return owner
    archived = metadata["app"].get("owner_username") or ""
    if archived:
        owner = users_repo.get_by_username(conn, archived)
        if owner is not None:
            return owner
    raise ImportAppError(
        f"The archive's owner '{archived or 'unknown'}' does not exist on this "
        "instance — pass --owner <local username>."
    )


def _extract_app_files(
    config: WALoaderConfig, archive_path: Path, old_slug: str, new_slug: str
) -> int:
    target_root = ensure_dir(layout.app_dir(config, new_slug))
    resolved_root = target_root.resolve()
    prefix = f"{old_slug}/"
    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir() or member.filename == "metadata.json":
                continue
            if not member.filename.startswith(prefix):
                raise ImportAppError(
                    f"Unexpected archive member {member.filename!r} "
                    f"(expected everything under '{prefix}')"
                )
            remainder = member.filename[len(prefix):]
            if ".." in PurePosixPath(remainder).parts:
                raise ImportAppError(f"Archive member escapes the app dir: {remainder!r}")
            target = (target_root / Path(*PurePosixPath(remainder).parts)).resolve()
            if resolved_root not in target.parents:
                raise ImportAppError(f"Archive member escapes the app dir: {remainder!r}")
            ensure_dir(target.parent)
            with archive.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted += 1
    return extracted


def import_app(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    archive_path: Path,
    *,
    owner_username: str | None = None,
    new_name: str | None = None,
    deploy: bool = True,
    actor: str = "",
    **test_seams,
) -> tuple[App, deployment.DeployResult | None]:
    metadata = app_archive.read_metadata(archive_path)  # validates archive_format
    if metadata.get("kind") != "app":
        raise ImportAppError(
            "This is a scope backup, not an app archive — full backups are "
            "restored with 'backupctl restore', not imported."
        )

    owner = _resolve_owner(conn, metadata, owner_username)
    name = (new_name or metadata["app"]["name"]).strip()
    availability = slugs.check_name_available(conn, name)
    if not availability.available:
        raise ImportAppError(
            f"{availability.reason}. Pass --name to import under a different "
            "name (soft-deleted apps keep their name reserved until purged)."
        )
    old_slug = metadata["app"]["slug"]
    new_slug = availability.slug

    def rewrite(stored: str) -> str:
        prefix = f"apps/{old_slug}/"
        if stored.startswith(prefix):
            return f"apps/{new_slug}/" + stored[len(prefix):]
        return stored

    app = apps_repo.create(
        conn, owner_id=owner.id, name=name, slug=new_slug,
        description=metadata["app"].get("description", ""),
        user_mgmt_enabled=bool(metadata["app"].get("user_mgmt_enabled")),
    )
    extracted = _extract_app_files(config, archive_path, old_slug, new_slug)

    for version in metadata["versions"]:
        versions_repo.create(
            conn, app_id=app.id, version_number=version["version_number"],
            manifest=json.loads(version["manifest_json"]),
            bundle_path=rewrite(version["bundle_path"]),
            source_path=rewrite(version["source_path"]),
            created_by=owner.id,
        )
    current = metadata["app"].get("current_version")
    if current:
        apps_repo.set_current_version(conn, app.id, current)

    include_data = bool(metadata.get("include_data", True))
    for concept_meta in metadata.get("dataset_concepts", []):
        concept = datasets_repo.create_concept(conn, app.id, concept_meta["name"])
        current_file = concept_meta.get("current_file")
        if include_data and current_file:
            datasets_repo.add_file(
                conn, concept_id=concept.id,
                original_filename=current_file["original_filename"],
                original_path=rewrite(current_file["original_path"]),
                canonical_path=rewrite(current_file["canonical_path"]),
                sheet_name=current_file.get("sheet_name"),
                schema=json.loads(current_file.get("schema_json") or "{}"),
                size_bytes=current_file.get("size_bytes", 0),
                uploaded_by=None,
            )

    for user_meta in metadata.get("app_users", []):
        app_user = app_users_repo.create(
            conn, app_id=app.id, username=user_meta["username"],
            email=user_meta.get("email", ""),
            password_hash=user_meta["password_hash"],
            observations=user_meta.get("observations", ""),
        )
        if not user_meta.get("is_active", 1):
            app_users_repo.set_active(conn, app_user.id, False)
        if include_data:
            for attachment in user_meta.get("attachments", []):
                app_users_repo.add_attachment(
                    conn, app_user_id=app_user.id,
                    filename=attachment["filename"],
                    stored_path=rewrite(attachment["stored_path"]),
                    note=attachment.get("note", ""),
                )

    audit_repo.record(
        conn, actor=actor, action="app.import", target=new_slug,
        details={"archive": archive_path.name, "from_slug": old_slug,
                 "files": extracted, "deploy": deploy},
    )
    conn.commit()
    log.info("imported %s -> %s (%d files)", archive_path.name, new_slug, extracted)

    result = None
    if deploy:
        result = deployment.rebuild_app(
            conn, config, apps_repo.get(conn, app.id), actor_id=owner.id, **test_seams
        )
    return apps_repo.get(conn, app.id), result
