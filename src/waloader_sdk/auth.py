"""Login support for child apps.

    from waloader_sdk.auth import require_login

    user = require_login()          # no-op (returns None) when user management
                                    # is disabled for this app
    ...
    logout_button()                 # sidebar-friendly logout
    change_password_form()          # self-service password change

The SDK is standalone inside the child venv: it talks to the WALoader DB
directly (path injected via WALOADER_DB_PATH) and verifies argon2 hashes with
argon2-cffi, which WALoader installs into every child venv.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from waloader_sdk._context import SdkContext, WALoaderEnvError, connect, get_context

__all__ = [
    "AuthError",
    "WALoaderEnvError",
    "change_password_form",
    "logout_button",
    "require_login",
]

_SESSION_KEY = "waloader_sdk_auth_user"
_MIN_PASSWORD_LENGTH = 8


class AuthError(RuntimeError):
    """Login problem with a user-safe message."""


# --- pure, testable core ----------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _verify(password_hash: str, password: str) -> bool:
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError

    try:
        return PasswordHasher().verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _hash(password: str) -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash(password)


def login_required(conn: sqlite3.Connection, app_slug: str) -> bool:
    row = conn.execute(
        "SELECT user_mgmt_enabled FROM apps WHERE slug=?", (app_slug,)
    ).fetchone()
    if row is None:
        raise AuthError(f"App {app_slug!r} is not registered in WALoader")
    return bool(row["user_mgmt_enabled"])


def authenticate(
    conn: sqlite3.Connection, app_slug: str, username: str, password: str
) -> dict[str, Any]:
    """Verify app-user credentials; returns a plain user dict."""
    row = conn.execute(
        """SELECT au.id, au.username, au.email, au.password_hash, au.is_active
           FROM app_users au JOIN apps a ON a.id = au.app_id
           WHERE a.slug = ? AND au.username = ?""",
        (app_slug, username),
    ).fetchone()
    if row is None or not _verify(row["password_hash"], password):
        raise AuthError("Invalid username or password")
    if not row["is_active"]:
        raise AuthError("This account has been deactivated")
    return {"id": row["id"], "username": row["username"], "email": row["email"]}


def change_password(
    conn: sqlite3.Connection, app_user_id: int, current_password: str, new_password: str
) -> None:
    row = conn.execute(
        "SELECT password_hash FROM app_users WHERE id=?", (app_user_id,)
    ).fetchone()
    if row is None or not _verify(row["password_hash"], current_password):
        raise AuthError("Current password is incorrect")
    if len(new_password) < _MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"New password must be at least {_MIN_PASSWORD_LENGTH} characters long"
        )
    conn.execute(
        "UPDATE app_users SET password_hash=?, updated_at=? WHERE id=?",
        (_hash(new_password), _now_iso(), app_user_id),
    )
    conn.commit()


# --- streamlit-facing helpers -------------------------------------------------


def _st():
    try:
        import streamlit as st
    except ImportError as exc:  # pragma: no cover - streamlit is always in child venvs
        raise RuntimeError("waloader_sdk.auth needs streamlit") from exc
    return st


def require_login(app_slug: str | None = None) -> dict[str, Any] | None:
    """Gate the app behind login when user management is enabled.

    Returns the logged-in user dict, or None when this app does not require
    login. Renders the login form and stops the script run otherwise.
    """
    context: SdkContext = get_context(app_slug)
    conn = connect(context)
    try:
        if not login_required(conn, context.app_slug):
            return None
        st = _st()
        if st.session_state.get(_SESSION_KEY):
            return st.session_state[_SESSION_KEY]

        st.title(context.app_name)
        st.subheader("Log in")
        with st.form("waloader_sdk_login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            try:
                user = authenticate(conn, context.app_slug, username, password)
            except AuthError as exc:
                st.error(str(exc))
            else:
                st.session_state[_SESSION_KEY] = user
                st.rerun()
        st.stop()
        return None  # unreachable; st.stop() raises
    finally:
        conn.close()


def logout_button(label: str = "Log out") -> None:
    st = _st()
    if st.session_state.get(_SESSION_KEY) and st.button(label):
        del st.session_state[_SESSION_KEY]
        st.rerun()


def change_password_form(app_slug: str | None = None) -> None:
    st = _st()
    user = st.session_state.get(_SESSION_KEY)
    if not user:
        st.info("Log in first to change your password.")
        return
    with st.form("waloader_sdk_change_password"):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Repeat new password", type="password")
        submitted = st.form_submit_button("Change password")
    if not submitted:
        return
    if new != confirm:
        st.error("The new passwords do not match")
        return
    context = get_context(app_slug)
    conn = connect(context)
    try:
        change_password(conn, user["id"], current, new)
    except AuthError as exc:
        st.error(str(exc))
    else:
        st.success("Password changed")
    finally:
        conn.close()
