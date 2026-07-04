"""Dashboard: app cards grid + per-app gear (configuration) dialog."""

from __future__ import annotations

import streamlit as st

from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import (
    app_users_service,
    authorization,
    deletion,
    deployment,
    health,
    lifecycle,
    processes,
)
from waloader.ui import common

CARDS_PER_ROW = 3


def _confirm_key(app_id: int) -> str:
    return f"confirm_action_{app_id}"


@st.dialog("Configure app", width="large")
def _gear_dialog(app_id: int) -> None:
    config = common.current_config()
    user = common.current_user(config)
    with common.open_conn(config) as conn:
        app = apps_repo.get(conn, app_id)
        authorization.require_app_manager(user, app)
        st.subheader(f"{app.name}  ·  {common.state_badge(app.state)}")

        # --- update code -------------------------------------------------
        with st.expander("Update code", expanded=False):
            st.caption(
                "Upload a new markdown bundle. The new version is built and "
                "tested before the running version is touched."
            )
            bundle = st.file_uploader(
                "New markdown bundle", type=["md", "txt", "markdown"],
                key=f"update_bundle_{app.id}",
            )
            if st.button("Deploy update", disabled=bundle is None,
                         key=f"deploy_update_{app.id}", type="primary"):
                with st.spinner(f"Deploying update for {app.name}…"):
                    result = deployment.redeploy(
                        conn, config, app, bundle.getvalue(), actor_id=user.id
                    )
                app = apps_repo.get(conn, app.id)
                common.store_deploy_outcome(app, result, health.app_url(config, app))
                st.rerun()

        # --- user management toggle ---------------------------------------
        enabled = st.toggle(
            "Users Management Support",
            value=bool(app.user_mgmt_enabled),
            key=f"umgmt_{app.id}",
            help="When enabled, this app requires its own users to log in "
                 "(manage them from the App users page).",
        )
        if enabled != bool(app.user_mgmt_enabled):
            app = app_users_service.set_user_management(conn, app, enabled,
                                                        actor=user.username)

        st.caption("*Dataset Concepts: use the **Datasets** page in the sidebar. "
                   "App users: the **App users** page.*")

        # --- lifecycle actions with confirmation ---------------------------
        st.divider()
        pending = st.session_state.get(_confirm_key(app.id))
        if pending:
            st.warning(f"Really **{pending}** '{app.name}'?"
                       + (" This archives the app and removes it from the "
                          "dashboard (recoverable until retention expires)."
                          if pending == "delete" else ""))
            yes, cancel = st.columns(2)
            if yes.button(f"Yes, {pending}", type="primary",
                          key=f"yes_{app.id}"):
                st.session_state.pop(_confirm_key(app.id), None)
                with st.spinner(f"{pending.capitalize()}ing…"):
                    if pending == "stop":
                        lifecycle.stop(conn, config, app, actor=user.username)
                    elif pending == "resume":
                        lifecycle.start(conn, config, app, actor=user.username)
                    elif pending == "restart":
                        lifecycle.restart(conn, config, app, actor=user.username)
                    elif pending == "delete":
                        deletion.soft_delete_app(conn, config, app,
                                                 actor=user.username)
                st.rerun()
            if cancel.button("Cancel", key=f"cancel_{app.id}"):
                st.session_state.pop(_confirm_key(app.id), None)
                st.rerun()
        else:
            running = processes.is_app_running(conn, app)
            columns = st.columns(4)
            if columns[0].button("Stop", disabled=not running,
                                 key=f"stop_{app.id}"):
                st.session_state[_confirm_key(app.id)] = "stop"
                st.rerun()
            if columns[1].button("Resume", disabled=running or
                                 app.current_version is None,
                                 key=f"resume_{app.id}"):
                st.session_state[_confirm_key(app.id)] = "resume"
                st.rerun()
            if columns[2].button("Restart", disabled=not running,
                                 key=f"restart_{app.id}"):
                st.session_state[_confirm_key(app.id)] = "restart"
                st.rerun()
            if columns[3].button("Delete", key=f"delete_{app.id}"):
                st.session_state[_confirm_key(app.id)] = "delete"
                st.rerun()


def _render_card(config, conn, app) -> None:
    with st.container(border=True):
        title, gear = st.columns([6, 1])
        title.markdown(f"**{app.name}**")
        if gear.button("⚙️", key=f"gear_{app.id}", help="Configure this app"):
            _gear_dialog(app.id)
        st.markdown(common.state_badge(app.state))
        details = []
        if app.current_version:
            details.append(f"v{app.current_version}")
        if app.port:
            details.append(f"port {app.port}")
        if app.description:
            details.append(app.description)
        if details:
            st.caption(" · ".join(details))
        rt = runtime_repo.get(conn, app.id)
        if rt and rt.last_healthy_at:
            st.caption(f"last healthy: {rt.last_healthy_at}")
        if app.state in ("failed", "deployment_failed") and rt and rt.last_failure_reason:
            st.caption(f"⚠ {rt.last_failure_reason}")
        if app.port and app.state == "running":
            st.link_button("Open", health.app_url(config, app),
                           use_container_width=True)


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    st.header("Your apps")
    common.render_deploy_outcome(config, user)

    with common.open_conn(config) as conn:
        show_all = False
        if authorization.is_admin(user):
            show_all = st.toggle("Show all users' apps", value=False)
        apps = (apps_repo.list_all(conn) if show_all
                else apps_repo.list_for_owner(conn, user.id))

        if not apps:
            st.info(
                "No apps yet. Use **Create new app** in the sidebar: upload the "
                "markdown bundle your coding LLM generated, pick a name, and "
                "WALoader deploys it."
            )
            return

        for start in range(0, len(apps), CARDS_PER_ROW):
            columns = st.columns(CARDS_PER_ROW)
            for column, app in zip(columns, apps[start:start + CARDS_PER_ROW],
                                   strict=False):
                with column:
                    _render_card(config, conn, app)
