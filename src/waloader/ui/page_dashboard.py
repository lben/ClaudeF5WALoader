"""Dashboard: app cards grid + per-app gear (configuration) dialog."""

from __future__ import annotations

import streamlit as st

from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import (
    app_migration,
    app_users_service,
    authorization,
    deletion,
    deployment,
    health,
    lifecycle,
    processes,
)
from waloader.ui import common, nav

CARDS_PER_ROW = 3


def _confirm_key(app_id: int) -> str:
    return f"confirm_action_{app_id}"


def _request_confirm(app_id: int, action: str) -> None:
    """Show the confirmation step for an action. MUST use a fragment-scoped
    rerun: a plain st.rerun() closes the st.dialog (that was the 'modal
    disappears when I click Restart' bug), fragment scope re-renders the dialog
    in place."""
    st.session_state[_confirm_key(app_id)] = action
    st.rerun(scope="fragment")


def _dismiss_confirm(app_id: int) -> None:
    st.session_state.pop(_confirm_key(app_id), None)
    st.rerun(scope="fragment")


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
                common.flash(
                    f"'{app.name}' updated" if result.ok
                    else f"'{app.name}' update failed — retry from the panel",
                    icon="✅" if result.ok else "⚠️",
                )
                nav.switch("dashboard")  # show the outcome on the dashboard
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
            common.toast_now(
                f"Users Management Support {'enabled' if enabled else 'disabled'} "
                f"for '{app.name}'"
            )
            st.rerun(scope="fragment")

        if app.user_mgmt_enabled and not app_users_service.code_enforces_login(
            config, app
        ):
            st.warning(
                "⚠ Users Management Support is ON, but this app's code never "
                "calls `require_login()`, so it will NOT ask visitors to log "
                "in. Ask your assistant to add the login gate (regenerate with "
                "the current authoring kit), then use **Update code** above. "
                "Manage the accounts on the **App users** page meanwhile."
            )

        link_columns = st.columns(2)
        if link_columns[0].button("🗃 Manage Dataset Concepts",
                                  key=f"goto_ds_{app.id}",
                                  use_container_width=True):
            st.session_state["preselect_app_slug"] = app.slug
            nav.switch("datasets")
        if link_columns[1].button("👥 Manage app users",
                                  key=f"goto_au_{app.id}",
                                  use_container_width=True):
            st.session_state["preselect_app_slug"] = app.slug
            nav.switch("app_users")

        # --- rebuild (after restore/import: venvs are never archived) ------
        if deployment.needs_rebuild(config, app):
            st.warning(
                "This app's virtualenv is missing (restored or imported?) — "
                "rebuild it from the preserved bundle before starting."
            )
            if st.button("Rebuild now", type="primary", key=f"rebuild_{app.id}"):
                with st.spinner(f"Rebuilding {app.name}…"):
                    result = deployment.rebuild_app(conn, config, app,
                                                    actor_id=user.id)
                app = apps_repo.get(conn, app.id)
                common.store_deploy_outcome(app, result, health.app_url(config, app))
                common.flash(
                    f"'{app.name}' rebuilt" if result.ok
                    else f"'{app.name}' rebuild failed — retry from the panel",
                    icon="✅" if result.ok else "⚠️",
                )
                nav.switch("dashboard")
                st.rerun()

        # --- export ----------------------------------------------------------
        with st.expander("Export app"):
            code_only = st.toggle("Code only (exclude datasets and user files)",
                                  key=f"exp_codeonly_{app.id}")
            if st.button("Create export archive", key=f"exp_go_{app.id}"):
                path = app_migration.export_app(
                    conn, config, app, include_data=not code_only,
                    actor=user.username,
                )
                st.session_state[f"exp_path_{app.id}"] = str(path)
                st.rerun(scope="fragment")  # stay in the dialog to show download
            exported = st.session_state.get(f"exp_path_{app.id}")
            if exported:
                from pathlib import Path

                export_path = Path(exported)
                if export_path.exists():
                    st.caption(f"`{export_path.name}` (also kept in backups/manual/)")
                    st.download_button(
                        "Download export", export_path.read_bytes(),
                        file_name=export_path.name, key=f"exp_dl_{app.id}",
                    )

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
                        outcome = lifecycle.stop(conn, config, app,
                                                 actor=user.username)
                        common.flash(outcome.message,
                                     icon="✅" if outcome.ok else "⚠️")
                    elif pending == "resume":
                        outcome = lifecycle.start(conn, config, app,
                                                  actor=user.username)
                        common.flash(outcome.message,
                                     icon="✅" if outcome.ok else "⚠️")
                    elif pending == "restart":
                        outcome = lifecycle.restart(conn, config, app,
                                                    actor=user.username)
                        common.flash(outcome.message,
                                     icon="✅" if outcome.ok else "⚠️")
                    elif pending == "delete":
                        deletion.soft_delete_app(conn, config, app,
                                                 actor=user.username)
                        common.flash(
                            f"'{app.name}' deleted — archived and recoverable "
                            "until retention expires"
                        )
                st.rerun()  # action done: close dialog, dashboard shows the flash
            if cancel.button("Cancel", key=f"cancel_{app.id}"):
                _dismiss_confirm(app.id)  # back to the action buttons, stay open
        else:
            running = processes.is_app_running(conn, app)
            columns = st.columns(4)
            if columns[0].button("Stop", disabled=not running,
                                 key=f"stop_{app.id}"):
                _request_confirm(app.id, "stop")
            if columns[1].button("Resume", disabled=running or
                                 app.current_version is None,
                                 key=f"resume_{app.id}"):
                _request_confirm(app.id, "resume")
            if columns[2].button("Restart", disabled=not running,
                                 key=f"restart_{app.id}"):
                _request_confirm(app.id, "restart")
            if columns[3].button("Delete", key=f"delete_{app.id}"):
                _request_confirm(app.id, "delete")


def _render_card(config, conn, app) -> None:
    with st.container(border=True):
        # top-align so the gear sits next to the first line of the name even
        # when a long name wraps; use_container_width makes the button fill the
        # column so its (centered) glyph is horizontally centered, not left-hugging.
        title, gear = st.columns([5, 1], vertical_alignment="top")
        title.markdown(f"**{app.name}**")
        if gear.button("⚙️", key=f"gear_{app.id}", help="Configure this app",
                       use_container_width=True):
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
        if deployment.needs_rebuild(config, app):
            st.caption("⚠ rebuild required (gear → Rebuild now)")
        if app.user_mgmt_enabled and not app_users_service.code_enforces_login(
            config, app
        ):
            st.caption("⚠ login ON but not enforced by the app code (gear ⚙️)")
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
