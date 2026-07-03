from __future__ import annotations

import sqlite3

from waloader.models import User
from waloader.util import utc_now_iso


def create(
    conn: sqlite3.Connection,
    username: str,
    email: str,
    password_hash: str,
    *,
    is_admin: bool = False,
) -> User:
    now = utc_now_iso()
    cur = conn.execute(
        """INSERT INTO users (username, email, password_hash, is_admin, is_active,
                              created_at, updated_at)
           VALUES (?,?,?,?,1,?,?)""",
        (username, email, password_hash, int(is_admin), now, now),
    )
    return get(conn, cur.lastrowid)


def get(conn: sqlite3.Connection, user_id: int) -> User:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise KeyError(f"No user with id {user_id}")
    return User.from_row(row)


def get_by_username(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return User.from_row(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [User.from_row(r) for r in rows]


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def update_profile(conn: sqlite3.Connection, user_id: int, *, email: str | None = None) -> User:
    if email is not None:
        conn.execute(
            "UPDATE users SET email=?, updated_at=? WHERE id=?",
            (email, utc_now_iso(), user_id),
        )
    return get(conn, user_id)


def set_password_hash(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
        (password_hash, utc_now_iso(), user_id),
    )


def set_active(conn: sqlite3.Connection, user_id: int, active: bool) -> None:
    conn.execute(
        "UPDATE users SET is_active=?, updated_at=? WHERE id=?",
        (int(active), utc_now_iso(), user_id),
    )


def set_admin(conn: sqlite3.Connection, user_id: int, is_admin: bool) -> None:
    conn.execute(
        "UPDATE users SET is_admin=?, updated_at=? WHERE id=?",
        (int(is_admin), utc_now_iso(), user_id),
    )
