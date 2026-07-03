"""WALoader-native app versioning: reconstruct bundles into version folders."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from waloader.config import WALoaderConfig
from waloader.models import App, AppVersion
from waloader.paths import ensure_dir, safe_join
from waloader.repositories import versions as versions_repo
from waloader.services import layout
from waloader.services.bundles import ParsedBundle
from waloader.util import utc_now_iso


def create_version(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    parsed: ParsedBundle,
    raw_bundle: bytes,
    *,
    created_by: int | None,
) -> AppVersion:
    """Write source tree + manifest + preserved bundle, record the version row.

    Reconstruction only ever writes inside this version's source/ directory
    (safe_join enforces containment on every file path).
    """
    number = versions_repo.next_version_number(conn, app.id)
    ensure_dir(layout.version_dir(config, app.slug, number))
    source = ensure_dir(layout.source_dir(config, app.slug, number))

    manifest_files = []
    for bundle_file in parsed.files:
        target = safe_join(source, bundle_file.path)
        ensure_dir(target.parent)
        content = bundle_file.content
        if content and not content.endswith("\n"):
            content += "\n"
        target.write_text(content, encoding="utf-8")
        manifest_files.append(
            {
                "path": bundle_file.path,
                "size": len(content.encode("utf-8")),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )

    bundle_file_path = layout.bundle_path(config, app.slug, number)
    bundle_file_path.write_bytes(raw_bundle)

    manifest = {
        "app_slug": app.slug,
        "version": number,
        "entrypoint": parsed.entrypoint,
        "bundle_app_name": parsed.app_name,
        "bundle_description": parsed.description,
        "files": manifest_files,
        "warnings": parsed.warnings,
        "created_at": utc_now_iso(),
    }
    layout.manifest_path(config, app.slug, number).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    version = versions_repo.create(
        conn,
        app_id=app.id,
        version_number=number,
        manifest=manifest,
        bundle_path=layout.relativize(config, bundle_file_path),
        source_path=layout.relativize(config, source),
        created_by=created_by,
    )
    conn.commit()
    return version
