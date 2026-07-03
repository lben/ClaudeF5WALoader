from __future__ import annotations

import sqlite3

from waloader.models import Deployment
from waloader.util import utc_now_iso


def start(
    conn: sqlite3.Connection,
    *,
    app_id: int,
    kind: str,
    version_id: int | None = None,
    log_path: str | None = None,
) -> Deployment:
    cur = conn.execute(
        """INSERT INTO deployments (app_id, version_id, kind, status, log_path, started_at)
           VALUES (?,?,?, 'in_progress', ?, ?)""",
        (app_id, version_id, kind, log_path, utc_now_iso()),
    )
    return get(conn, cur.lastrowid)


def get(conn: sqlite3.Connection, deployment_id: int) -> Deployment:
    row = conn.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
    if row is None:
        raise KeyError(f"No deployment with id {deployment_id}")
    return Deployment.from_row(row)


def finish(
    conn: sqlite3.Connection,
    deployment_id: int,
    *,
    status: str,
    error_summary: str | None = None,
    version_id: int | None = None,
) -> None:
    conn.execute(
        """UPDATE deployments
           SET status=?, error_summary=?, finished_at=?,
               version_id=COALESCE(?, version_id)
           WHERE id=?""",
        (status, error_summary, utc_now_iso(), version_id, deployment_id),
    )


def list_for_app(conn: sqlite3.Connection, app_id: int, limit: int = 20) -> list[Deployment]:
    rows = conn.execute(
        "SELECT * FROM deployments WHERE app_id=? ORDER BY id DESC LIMIT ?", (app_id, limit)
    ).fetchall()
    return [Deployment.from_row(r) for r in rows]
