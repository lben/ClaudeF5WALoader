from __future__ import annotations

import sqlite3

from waloader.models import App
from waloader.util import utc_now_iso


def create(
    conn: sqlite3.Connection,
    *,
    owner_id: int,
    name: str,
    slug: str,
    description: str = "",
    user_mgmt_enabled: bool = False,
) -> App:
    now = utc_now_iso()
    cur = conn.execute(
        """INSERT INTO apps (owner_id, name, slug, description, state, user_mgmt_enabled,
                             created_at, updated_at)
           VALUES (?,?,?,?, 'created', ?, ?, ?)""",
        (owner_id, name, slug, description, int(user_mgmt_enabled), now, now),
    )
    return get(conn, cur.lastrowid)


def get(conn: sqlite3.Connection, app_id: int) -> App:
    row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
    if row is None:
        raise KeyError(f"No app with id {app_id}")
    return App.from_row(row)


def get_by_slug(conn: sqlite3.Connection, slug: str) -> App | None:
    row = conn.execute("SELECT * FROM apps WHERE slug=?", (slug,)).fetchone()
    return App.from_row(row) if row else None


def list_for_owner(
    conn: sqlite3.Connection, owner_id: int, *, include_deleted: bool = False
) -> list[App]:
    sql = "SELECT * FROM apps WHERE owner_id=?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    rows = conn.execute(sql + " ORDER BY name", (owner_id,)).fetchall()
    return [App.from_row(r) for r in rows]


def list_all(conn: sqlite3.Connection, *, include_deleted: bool = False) -> list[App]:
    sql = "SELECT * FROM apps"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    rows = conn.execute(sql + " ORDER BY name").fetchall()
    return [App.from_row(r) for r in rows]


def name_taken(conn: sqlite3.Connection, name: str, *, exclude_app_id: int | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM apps WHERE name=? AND (? IS NULL OR id != ?)",
        (name, exclude_app_id, exclude_app_id),
    ).fetchone()
    return row is not None


def slug_taken(conn: sqlite3.Connection, slug: str, *, exclude_app_id: int | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM apps WHERE slug=? AND (? IS NULL OR id != ?)",
        (slug, exclude_app_id, exclude_app_id),
    ).fetchone()
    return row is not None


def used_ports(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT port FROM apps WHERE port IS NOT NULL").fetchall()
    return {row["port"] for row in rows}


def _touch(conn: sqlite3.Connection, app_id: int) -> None:
    conn.execute("UPDATE apps SET updated_at=? WHERE id=?", (utc_now_iso(), app_id))


def set_state(conn: sqlite3.Connection, app_id: int, state: str) -> None:
    conn.execute("UPDATE apps SET state=? WHERE id=?", (state, app_id))
    _touch(conn, app_id)


def set_port(conn: sqlite3.Connection, app_id: int, port: int | None) -> None:
    conn.execute("UPDATE apps SET port=? WHERE id=?", (port, app_id))
    _touch(conn, app_id)


def set_caddy_route(conn: sqlite3.Connection, app_id: int, route: str | None) -> None:
    conn.execute("UPDATE apps SET caddy_route=? WHERE id=?", (route, app_id))
    _touch(conn, app_id)


def set_current_version(conn: sqlite3.Connection, app_id: int, version_number: int) -> None:
    conn.execute("UPDATE apps SET current_version=? WHERE id=?", (version_number, app_id))
    _touch(conn, app_id)


def set_user_mgmt(conn: sqlite3.Connection, app_id: int, enabled: bool) -> None:
    conn.execute("UPDATE apps SET user_mgmt_enabled=? WHERE id=?", (int(enabled), app_id))
    _touch(conn, app_id)


def set_description(conn: sqlite3.Connection, app_id: int, description: str) -> None:
    conn.execute("UPDATE apps SET description=? WHERE id=?", (description, app_id))
    _touch(conn, app_id)


def set_last_deploy_error(conn: sqlite3.Connection, app_id: int, error: str | None) -> None:
    conn.execute("UPDATE apps SET last_deploy_error=? WHERE id=?", (error, app_id))
    _touch(conn, app_id)


def mark_deleted(
    conn: sqlite3.Connection, app_id: int, *, archive_path: str, purge_after: str
) -> None:
    conn.execute(
        "UPDATE apps SET deleted_at=?, archive_path=?, purge_after=?, state='deleted' WHERE id=?",
        (utc_now_iso(), archive_path, purge_after, app_id),
    )
    _touch(conn, app_id)


def list_purge_due(conn: sqlite3.Connection, now_iso: str) -> list[App]:
    rows = conn.execute(
        "SELECT * FROM apps WHERE deleted_at IS NOT NULL AND purge_after <= ?", (now_iso,)
    ).fetchall()
    return [App.from_row(r) for r in rows]


def hard_delete(conn: sqlite3.Connection, app_id: int) -> None:
    conn.execute("DELETE FROM apps WHERE id=?", (app_id,))
