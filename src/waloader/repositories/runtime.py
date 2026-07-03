from __future__ import annotations

import sqlite3

from waloader.models import AppRuntime
from waloader.util import utc_now_iso


def get(conn: sqlite3.Connection, app_id: int) -> AppRuntime | None:
    row = conn.execute("SELECT * FROM app_runtime WHERE app_id=?", (app_id,)).fetchone()
    return AppRuntime.from_row(row) if row else None


def upsert_started(
    conn: sqlite3.Connection, app_id: int, *, pid: int, pid_create_time: float
) -> None:
    now = utc_now_iso()
    conn.execute(
        """INSERT INTO app_runtime (app_id, pid, pid_create_time, started_at,
                                    consecutive_failures, deployed_healthy)
           VALUES (?,?,?,?,0,0)
           ON CONFLICT(app_id) DO UPDATE SET
               pid=excluded.pid, pid_create_time=excluded.pid_create_time,
               started_at=excluded.started_at, consecutive_failures=0,
               deployed_healthy=0, last_failure_reason=NULL""",
        (app_id, pid, pid_create_time, now),
    )


def clear_process(conn: sqlite3.Connection, app_id: int) -> None:
    conn.execute(
        "UPDATE app_runtime SET pid=NULL, pid_create_time=NULL WHERE app_id=?", (app_id,)
    )


def record_healthy(conn: sqlite3.Connection, app_id: int) -> None:
    now = utc_now_iso()
    conn.execute(
        """UPDATE app_runtime SET last_check_at=?, last_healthy_at=?, consecutive_failures=0
           WHERE app_id=?""",
        (now, now, app_id),
    )


def record_unhealthy(conn: sqlite3.Connection, app_id: int, reason: str) -> int:
    """Record a failed check; returns the new consecutive failure count."""
    now = utc_now_iso()
    conn.execute(
        """UPDATE app_runtime
           SET last_check_at=?, last_failed_at=?, last_failure_reason=?,
               consecutive_failures=consecutive_failures+1
           WHERE app_id=?""",
        (now, now, reason, app_id),
    )
    row = conn.execute(
        "SELECT consecutive_failures FROM app_runtime WHERE app_id=?", (app_id,)
    ).fetchone()
    return row[0] if row else 0


def set_deployed_healthy(conn: sqlite3.Connection, app_id: int, value: bool) -> None:
    conn.execute(
        "UPDATE app_runtime SET deployed_healthy=? WHERE app_id=?", (int(value), app_id)
    )
