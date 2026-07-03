from __future__ import annotations

import sqlite3

from waloader.models import AppUser, AppUserAttachment
from waloader.util import utc_now_iso


def create(
    conn: sqlite3.Connection,
    *,
    app_id: int,
    username: str,
    email: str,
    password_hash: str,
    observations: str = "",
) -> AppUser:
    now = utc_now_iso()
    cur = conn.execute(
        """INSERT INTO app_users (app_id, username, email, password_hash, is_active,
                                  observations, created_at, updated_at)
           VALUES (?,?,?,?,1,?,?,?)""",
        (app_id, username, email, password_hash, observations, now, now),
    )
    return get(conn, cur.lastrowid)


def get(conn: sqlite3.Connection, app_user_id: int) -> AppUser:
    row = conn.execute("SELECT * FROM app_users WHERE id=?", (app_user_id,)).fetchone()
    if row is None:
        raise KeyError(f"No app user with id {app_user_id}")
    return AppUser.from_row(row)


def get_by_username(conn: sqlite3.Connection, app_id: int, username: str) -> AppUser | None:
    row = conn.execute(
        "SELECT * FROM app_users WHERE app_id=? AND username=?", (app_id, username)
    ).fetchone()
    return AppUser.from_row(row) if row else None


def list_for_app(conn: sqlite3.Connection, app_id: int) -> list[AppUser]:
    rows = conn.execute(
        "SELECT * FROM app_users WHERE app_id=? ORDER BY username", (app_id,)
    ).fetchall()
    return [AppUser.from_row(r) for r in rows]


def update(
    conn: sqlite3.Connection,
    app_user_id: int,
    *,
    email: str | None = None,
    observations: str | None = None,
) -> AppUser:
    if email is not None:
        conn.execute(
            "UPDATE app_users SET email=?, updated_at=? WHERE id=?",
            (email, utc_now_iso(), app_user_id),
        )
    if observations is not None:
        conn.execute(
            "UPDATE app_users SET observations=?, updated_at=? WHERE id=?",
            (observations, utc_now_iso(), app_user_id),
        )
    return get(conn, app_user_id)


def set_active(conn: sqlite3.Connection, app_user_id: int, active: bool) -> None:
    conn.execute(
        "UPDATE app_users SET is_active=?, updated_at=? WHERE id=?",
        (int(active), utc_now_iso(), app_user_id),
    )


def set_password_hash(conn: sqlite3.Connection, app_user_id: int, password_hash: str) -> None:
    conn.execute(
        "UPDATE app_users SET password_hash=?, updated_at=? WHERE id=?",
        (password_hash, utc_now_iso(), app_user_id),
    )


def delete(conn: sqlite3.Connection, app_user_id: int) -> None:
    conn.execute("DELETE FROM app_users WHERE id=?", (app_user_id,))


def add_attachment(
    conn: sqlite3.Connection,
    *,
    app_user_id: int,
    filename: str,
    stored_path: str,
    note: str = "",
) -> AppUserAttachment:
    cur = conn.execute(
        """INSERT INTO app_user_attachments (app_user_id, filename, stored_path, note,
                                             uploaded_at)
           VALUES (?,?,?,?,?)""",
        (app_user_id, filename, stored_path, note, utc_now_iso()),
    )
    row = conn.execute(
        "SELECT * FROM app_user_attachments WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return AppUserAttachment.from_row(row)


def list_attachments(conn: sqlite3.Connection, app_user_id: int) -> list[AppUserAttachment]:
    rows = conn.execute(
        "SELECT * FROM app_user_attachments WHERE app_user_id=? ORDER BY id", (app_user_id,)
    ).fetchall()
    return [AppUserAttachment.from_row(r) for r in rows]


def delete_attachment(conn: sqlite3.Connection, attachment_id: int) -> None:
    conn.execute("DELETE FROM app_user_attachments WHERE id=?", (attachment_id,))
