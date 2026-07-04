"""Shared UI plumbing: config/DB access, auth gate, deploy-outcome panel.

Every page is a thin client: all behavior lives in waloader.services.
Connections are opened per action (SQLite + WAL handles multi-thread reruns).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import streamlit as st

from waloader import db
from waloader.config import WALoaderConfig, apply_db_overrides, load_config
from waloader.models import App, User
from waloader.repositories import apps as apps_repo
from waloader.repositories import settings as settings_repo
from waloader.repositories import users as users_repo
from waloader.services import deployment, health, reconciliation, users_service

SESSION_USER_KEY = "waloader_user_id"
SESSION_DEPLOY_OUTCOME = "waloader_deploy_outcome"

STATE_BADGES = {
    "created": "⚪ created",
    "deploying": "🟡 deploying",
    "deployment_failed": "🔴 deployment failed",
    "running": "🟢 running",
    "stopped": "⚪ stopped",
    "failed": "🔴 failed",
    "deleted": "🗑 deleted",
    "pending_delete": "🗑 pending delete",
}


def state_badge(state: str) -> str:
    return STATE_BADGES.get(state, state)


def current_config() -> WALoaderConfig:
    """Fresh effective config each rerun (TOML + DB overrides). Cheap."""
    loaded = load_config()
    try:
        conn = db.connect(loaded.config.database_path)
        try:
            overrides = settings_repo.get_all(conn)
        finally:
            conn.close()
        return apply_db_overrides(loaded, overrides).config
    except Exception:
        return loaded.config  # DB not initialized yet: boot handles migration


@contextmanager
def open_conn(config: WALoaderConfig):
    conn = db.connect(config.database_path)
    try:
        yield conn
    finally:
        conn.close()


@st.cache_resource(show_spinner=False)
def boot_once(db_path: str) -> bool:
    """One-time per UI process: migrations, reconciliation, background worker."""
    config = current_config()
    conn = db.connect(config.database_path)
    try:
        if config.database.auto_migrate:
            db.migrate(conn)
        reconciliation.reconcile(conn, config)
    finally:
        conn.close()
    if config.health.background_enabled:
        from waloader.services.background import start_background_worker

        start_background_worker()
    return True


# --- authentication gate -----------------------------------------------------


def _render_bootstrap(conn: sqlite3.Connection) -> None:
    st.title("WALoader")
    st.subheader("First-time setup — create the administrator account")
    with st.form("bootstrap_admin"):
        username = st.text_input("Admin username")
        email = st.text_input("Email (crash notifications go here)")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Repeat password", type="password")
        submitted = st.form_submit_button("Create administrator")
    if submitted:
        if password != confirm:
            st.error("The passwords do not match")
            return
        try:
            user = users_service.bootstrap_admin(
                conn, username=username, email=email, password=password
            )
        except (users_service.UserValidationError, ValueError) as exc:
            st.error(str(exc))
            return
        st.session_state[SESSION_USER_KEY] = user.id
        st.rerun()


def _render_login(conn: sqlite3.Connection) -> None:
    st.title("WALoader")
    st.subheader("Log in")
    with st.form("waloader_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        try:
            user = users_service.authenticate(conn, username, password)
        except users_service.AuthError as exc:
            st.error(str(exc))
            return
        st.session_state[SESSION_USER_KEY] = user.id
        st.rerun()


def require_user(config: WALoaderConfig) -> User:
    """Gate: bootstrap screen when no users exist, else login. Stops if unauthenticated."""
    with open_conn(config) as conn:
        if users_service.bootstrap_needed(conn):
            _render_bootstrap(conn)
            st.stop()
        user_id = st.session_state.get(SESSION_USER_KEY)
        if user_id is not None:
            try:
                user = users_repo.get(conn, user_id)
                if user.is_active:
                    return user
            except KeyError:
                pass
        _render_login(conn)
        st.stop()
    raise RuntimeError("unreachable")  # st.stop() always raises


def current_user(config: WALoaderConfig) -> User:
    """For pages: the gate in app.py already ran."""
    with open_conn(config) as conn:
        return users_repo.get(conn, st.session_state[SESSION_USER_KEY])


def logout() -> None:
    st.session_state.pop(SESSION_USER_KEY, None)
    st.rerun()


# --- deployment outcome panel (shared by create + update + retry) -------------


def store_deploy_outcome(app: App, result: deployment.DeployResult, url: str | None) -> None:
    st.session_state[SESSION_DEPLOY_OUTCOME] = {
        "ok": result.ok,
        "app_id": app.id,
        "app_name": app.name,
        "kind": result.kind,
        "url": url,
        "error_block": None if result.ok else result.error_block(),
        "error_summary": result.error_summary,
    }


def render_deploy_outcome(config: WALoaderConfig, user: User) -> None:
    """Success/error panel with copyable blocks and retry-upload, per G01 §4.2."""
    outcome = st.session_state.get(SESSION_DEPLOY_OUTCOME)
    if not outcome:
        return
    with st.container(border=True):
        if outcome["ok"]:
            st.success(
                f"**{outcome['app_name']}** deployed successfully "
                f"({outcome['kind']})."
            )
            st.write("Your app's address (copy or open):")
            st.code(outcome["url"], language=None)
            columns = st.columns([1, 1, 4])
            columns[0].link_button("Open app", outcome["url"])
            if columns[1].button("Dismiss", key="dismiss_outcome"):
                st.session_state.pop(SESSION_DEPLOY_OUTCOME, None)
                st.rerun()
        else:
            st.error(
                f"Deployment of **{outcome['app_name']}** failed "
                f"({outcome['error_summary'] or 'see details below'})."
            )
            st.write("Full details (copy this into your coding LLM to get a fix):")
            st.code(outcome["error_block"], language=None)
            fixed = st.file_uploader(
                "Upload the fixed markdown bundle and retry",
                type=["md", "txt", "markdown"],
                key="retry_bundle",
            )
            columns = st.columns([1, 1, 4])
            retry_disabled = fixed is None
            if columns[0].button("Retry deployment", disabled=retry_disabled,
                                 type="primary"):
                with open_conn(config) as conn:
                    app = apps_repo.get(conn, outcome["app_id"])
                    with st.spinner(f"Redeploying {app.name}…"):
                        result = deployment.redeploy(
                            conn, config, app, fixed.getvalue(), actor_id=user.id
                        )
                    app = apps_repo.get(conn, app.id)
                    store_deploy_outcome(app, result, health.app_url(config, app))
                st.rerun()
            if columns[1].button("Dismiss", key="dismiss_outcome_err"):
                st.session_state.pop(SESSION_DEPLOY_OUTCOME, None)
                st.rerun()
