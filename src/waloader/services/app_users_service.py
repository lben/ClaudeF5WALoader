"""Per-app user management (the reusable module, service side).

App owners manage the users of their own apps here; child apps enforce login
through waloader_sdk.auth, which reads the same tables. Passwords are argon2
hashes — identical scheme to platform users.
"""

from __future__ import annotations

import re
import shutil
import sqlite3

from waloader.config import WALoaderConfig
from waloader.models import App, AppUser, AppUserAttachment
from waloader.paths import ensure_dir
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.services import layout, security
from waloader.services.users_service import USERNAME_RE, AuthError


class AppUserError(ValueError):
    pass


def set_user_management(
    conn: sqlite3.Connection, app: App, enabled: bool, *, actor: str = ""
) -> App:
    apps_repo.set_user_mgmt(conn, app.id, enabled)
    audit_repo.record(
        conn, actor=actor,
        action="app.user_mgmt.enable" if enabled else "app.user_mgmt.disable",
        target=app.slug,
    )
    conn.commit()
    return apps_repo.get(conn, app.id)


def login_required(app: App) -> bool:
    return bool(app.user_mgmt_enabled)


LOGIN_SDK_MARKERS = ("require_login", "waloader_sdk.auth")


def code_enforces_login(config: WALoaderConfig, app: App) -> bool:
    """Whether the app's current-version source actually calls the login SDK.

    WALoader cannot inject login into arbitrary child code — the app must call
    ``require_login()`` itself. If Users Management Support is ON but this is
    False, the setting is silently ignored, so the UI warns the owner. Scans
    the reconstructed source of the current version for the SDK markers.
    """
    if app.current_version is None:
        return False
    source = layout.source_dir(config, app.slug, app.current_version)
    if not source.exists():
        return False
    for py in source.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(marker in text for marker in LOGIN_SDK_MARKERS):
            return True
    return False


def create_app_user(
    conn: sqlite3.Connection,
    app: App,
    *,
    username: str,
    email: str,
    password: str,
    observations: str = "",
    actor: str = "",
) -> AppUser:
    if not USERNAME_RE.match(username):
        raise AppUserError(
            "Username must be 2-63 characters: letters, digits, '.', '_' or '-'"
        )
    if app_users_repo.get_by_username(conn, app.id, username) is not None:
        raise AppUserError(
            f"User '{username}' already exists for app '{app.slug}'"
        )
    security.validate_password_strength(password)
    user = app_users_repo.create(
        conn, app_id=app.id, username=username, email=email,
        password_hash=security.hash_password(password), observations=observations,
    )
    audit_repo.record(conn, actor=actor, action="app_user.create",
                      target=f"{app.slug}:{username}")
    conn.commit()
    return user


def update_app_user(
    conn: sqlite3.Connection,
    app: App,
    app_user_id: int,
    *,
    email: str | None = None,
    observations: str | None = None,
    actor: str = "",
) -> AppUser:
    user = app_users_repo.update(conn, app_user_id, email=email,
                                 observations=observations)
    audit_repo.record(conn, actor=actor, action="app_user.update",
                      target=f"{app.slug}:{user.username}")
    conn.commit()
    return user


def set_app_user_active(
    conn: sqlite3.Connection, app: App, app_user_id: int, active: bool, *, actor: str = ""
) -> None:
    user = app_users_repo.get(conn, app_user_id)
    app_users_repo.set_active(conn, app_user_id, active)
    audit_repo.record(
        conn, actor=actor,
        action="app_user.reactivate" if active else "app_user.deactivate",
        target=f"{app.slug}:{user.username}",
    )
    conn.commit()


def delete_app_user(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, app_user_id: int,
    *, actor: str = ""
) -> None:
    user = app_users_repo.get(conn, app_user_id)
    app_users_repo.delete(conn, app_user_id)
    shutil.rmtree(layout.user_files_dir(config, app.slug, app_user_id),
                  ignore_errors=True)
    audit_repo.record(conn, actor=actor, action="app_user.delete",
                      target=f"{app.slug}:{user.username}")
    conn.commit()


def authenticate_app_user(
    conn: sqlite3.Connection, app: App, username: str, password: str
) -> AppUser:
    user = app_users_repo.get_by_username(conn, app.id, username)
    if user is None or not security.verify_password(user.password_hash, password):
        raise AuthError("Invalid username or password")
    if not user.is_active:
        raise AuthError("This account has been deactivated")
    return user


def change_app_user_password(
    conn: sqlite3.Connection, app: App, app_user_id: int,
    current_password: str, new_password: str,
) -> None:
    user = app_users_repo.get(conn, app_user_id)
    if not security.verify_password(user.password_hash, current_password):
        raise AuthError("Current password is incorrect")
    security.validate_password_strength(new_password)
    app_users_repo.set_password_hash(
        conn, app_user_id, security.hash_password(new_password)
    )
    audit_repo.record(conn, actor=user.username, action="app_user.change_password",
                      target=f"{app.slug}:{user.username}")
    conn.commit()


def owner_reset_app_user_password(
    conn: sqlite3.Connection, app: App, app_user_id: int, new_password: str,
    *, actor: str = ""
) -> None:
    security.validate_password_strength(new_password)
    user = app_users_repo.get(conn, app_user_id)
    app_users_repo.set_password_hash(
        conn, app_user_id, security.hash_password(new_password)
    )
    audit_repo.record(conn, actor=actor, action="app_user.reset_password",
                      target=f"{app.slug}:{user.username}")
    conn.commit()


# --- attachments (access-justification evidence etc.) ----------------------


def add_attachment(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    app_user_id: int,
    *,
    filename: str,
    data: bytes,
    note: str = "",
    actor: str = "",
) -> AppUserAttachment:
    if not filename.strip():
        raise AppUserError("Attachment needs a filename")
    directory = ensure_dir(layout.user_files_dir(config, app.slug, app_user_id))
    safe_name = re.sub(r"[^A-Za-z0-9._\-]", "_", filename)
    target = directory / safe_name
    counter = 1
    while target.exists():  # never silently overwrite evidence files
        target = directory / f"{counter}_{safe_name}"
        counter += 1
    target.write_bytes(data)
    attachment = app_users_repo.add_attachment(
        conn, app_user_id=app_user_id, filename=filename,
        stored_path=layout.relativize(config, target), note=note,
    )
    audit_repo.record(conn, actor=actor, action="app_user.attachment.add",
                      target=f"{app.slug}:{app_user_id}:{filename}")
    conn.commit()
    return attachment


def delete_attachment(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, attachment_id: int,
    *, actor: str = ""
) -> None:
    rows = conn.execute(
        "SELECT * FROM app_user_attachments WHERE id=?", (attachment_id,)
    ).fetchone()
    if rows is None:
        return
    layout.resolve(config, rows["stored_path"]).unlink(missing_ok=True)
    app_users_repo.delete_attachment(conn, attachment_id)
    audit_repo.record(conn, actor=actor, action="app_user.attachment.delete",
                      target=f"{app.slug}:{rows['filename']}")
    conn.commit()
