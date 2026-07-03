from __future__ import annotations

import sqlite3
from typing import Any

from waloader.config import decode_setting, encode_setting
from waloader.util import utc_now_iso


def get_all(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    return {row["key"]: decode_setting(row["value_json"]) for row in rows}


def get(conn: sqlite3.Connection, key: str) -> Any | None:
    row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    return decode_setting(row["value_json"]) if row else None


def set_value(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """INSERT INTO settings (key, value_json, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                                          updated_at=excluded.updated_at""",
        (key, encode_setting(value), utc_now_iso()),
    )


def delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
