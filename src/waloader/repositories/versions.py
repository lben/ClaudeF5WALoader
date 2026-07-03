from __future__ import annotations

import json
import sqlite3
from typing import Any

from waloader.models import AppVersion
from waloader.util import utc_now_iso


def next_version_number(conn: sqlite3.Connection, app_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 FROM app_versions WHERE app_id=?",
        (app_id,),
    ).fetchone()
    return row[0]


def create(
    conn: sqlite3.Connection,
    *,
    app_id: int,
    version_number: int,
    manifest: dict[str, Any],
    bundle_path: str,
    source_path: str,
    created_by: int | None,
) -> AppVersion:
    cur = conn.execute(
        """INSERT INTO app_versions (app_id, version_number, manifest_json, bundle_path,
                                     source_path, created_by, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (app_id, version_number, json.dumps(manifest), bundle_path, source_path, created_by,
         utc_now_iso()),
    )
    return get(conn, cur.lastrowid)


def get(conn: sqlite3.Connection, version_id: int) -> AppVersion:
    row = conn.execute("SELECT * FROM app_versions WHERE id=?", (version_id,)).fetchone()
    if row is None:
        raise KeyError(f"No app version with id {version_id}")
    return AppVersion.from_row(row)


def get_by_number(conn: sqlite3.Connection, app_id: int, version_number: int) -> AppVersion | None:
    row = conn.execute(
        "SELECT * FROM app_versions WHERE app_id=? AND version_number=?",
        (app_id, version_number),
    ).fetchone()
    return AppVersion.from_row(row) if row else None


def list_for_app(conn: sqlite3.Connection, app_id: int) -> list[AppVersion]:
    rows = conn.execute(
        "SELECT * FROM app_versions WHERE app_id=? ORDER BY version_number", (app_id,)
    ).fetchall()
    return [AppVersion.from_row(r) for r in rows]
