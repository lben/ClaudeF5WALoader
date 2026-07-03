from __future__ import annotations

import sqlite3

from waloader.util import utc_now_iso


def was_sent(conn: sqlite3.Connection, app_id: int, event_key: str) -> bool:
    row = conn.execute(
        "SELECT id FROM notifications_sent WHERE app_id=? AND event_key=?",
        (app_id, event_key),
    ).fetchone()
    return row is not None


def mark_sent(conn: sqlite3.Connection, app_id: int, event_key: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO notifications_sent (app_id, event_key, sent_at)
           VALUES (?,?,?)""",
        (app_id, event_key, utc_now_iso()),
    )


def clear_for_app(conn: sqlite3.Connection, app_id: int) -> None:
    conn.execute("DELETE FROM notifications_sent WHERE app_id=?", (app_id,))
