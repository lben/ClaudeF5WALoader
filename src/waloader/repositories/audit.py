from __future__ import annotations

import json
import sqlite3
from typing import Any

from waloader.util import utc_now_iso


def record(
    conn: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    target: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO audit_log (actor, action, target, details_json, created_at)
           VALUES (?,?,?,?,?)""",
        (actor, action, target, json.dumps(details or {}), utc_now_iso()),
    )


def recent(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
