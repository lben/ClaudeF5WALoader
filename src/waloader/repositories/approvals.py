from __future__ import annotations

import sqlite3

from waloader.util import utc_now_iso


def is_approved(conn: sqlite3.Connection, app_id: int, requirement: str) -> bool:
    row = conn.execute(
        "SELECT id FROM dependency_approvals WHERE app_id=? AND requirement=?",
        (app_id, requirement),
    ).fetchone()
    return row is not None


def approve(
    conn: sqlite3.Connection, app_id: int, requirement: str, approved_by: int | None
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO dependency_approvals (app_id, requirement, approved_by,
                                                       approved_at)
           VALUES (?,?,?,?)""",
        (app_id, requirement, approved_by, utc_now_iso()),
    )


def list_for_app(conn: sqlite3.Connection, app_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT requirement FROM dependency_approvals WHERE app_id=?", (app_id,)
    ).fetchall()
    return [row["requirement"] for row in rows]
