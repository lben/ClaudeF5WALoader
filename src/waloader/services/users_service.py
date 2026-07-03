"""WALoader platform user management: creation, login, passwords, bootstrap."""

from __future__ import annotations

import re
import sqlite3

from waloader.models import User
from waloader.repositories import audit as audit_repo
from waloader.repositories import users as users_repo
from waloader.services import security

USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-]{1,62}$")


class AuthError(Exception):
    """Bad credentials or inactive account. Message is safe to show users."""


class UserValidationError(ValueError):
    pass


def _validate_new_user(conn: sqlite3.Connection, username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise UserValidationError(
            "Username must be 2-63 characters: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    if users_repo.get_by_username(conn, username) is not None:
        raise UserValidationError(f"Username '{username}' is already taken")
    security.validate_password_strength(password)


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    email: str,
    password: str,
    is_admin: bool = False,
    actor: str = "",
) -> User:
    _validate_new_user(conn, username, password)
    user = users_repo.create(
        conn, username, email, security.hash_password(password), is_admin=is_admin
    )
    audit_repo.record(conn, actor=actor or username, action="user.create", target=username,
                      details={"is_admin": is_admin})
    conn.commit()
    return user


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User:
    user = users_repo.get_by_username(conn, username)
    if user is None or not security.verify_password(user.password_hash, password):
        raise AuthError("Invalid username or password")
    if not user.is_active:
        raise AuthError("This account has been deactivated")
    return user


def change_password(
    conn: sqlite3.Connection, user_id: int, current_password: str, new_password: str
) -> None:
    user = users_repo.get(conn, user_id)
    if not security.verify_password(user.password_hash, current_password):
        raise AuthError("Current password is incorrect")
    security.validate_password_strength(new_password)
    users_repo.set_password_hash(conn, user_id, security.hash_password(new_password))
    audit_repo.record(conn, actor=user.username, action="user.change_password",
                      target=user.username)
    conn.commit()


def admin_reset_password(
    conn: sqlite3.Connection, user_id: int, new_password: str, *, actor: str
) -> None:
    security.validate_password_strength(new_password)
    user = users_repo.get(conn, user_id)
    users_repo.set_password_hash(conn, user_id, security.hash_password(new_password))
    audit_repo.record(conn, actor=actor, action="user.reset_password", target=user.username)
    conn.commit()


def set_active(conn: sqlite3.Connection, user_id: int, active: bool, *, actor: str) -> None:
    user = users_repo.get(conn, user_id)
    users_repo.set_active(conn, user_id, active)
    audit_repo.record(
        conn, actor=actor, action="user.activate" if active else "user.deactivate",
        target=user.username,
    )
    conn.commit()


def bootstrap_needed(conn: sqlite3.Connection) -> bool:
    return users_repo.count(conn) == 0


def bootstrap_admin(
    conn: sqlite3.Connection, *, username: str, email: str, password: str
) -> User:
    """Create the very first (admin) account. Only valid while no users exist."""
    if not bootstrap_needed(conn):
        raise UserValidationError("Bootstrap is only allowed when no users exist yet")
    return create_user(
        conn, username=username, email=email, password=password, is_admin=True,
        actor="bootstrap",
    )
