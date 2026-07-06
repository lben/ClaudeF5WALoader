"""Per-app user management for app owners (G01 §4.16)."""

from __future__ import annotations

import streamlit as st

from waloader.repositories import app_users as app_users_repo
from waloader.repositories import apps as apps_repo
from waloader.services import app_users_service as aus
from waloader.services import authorization, layout, security
from waloader.services.users_service import AuthError
from waloader.ui import common


def _manageable_apps(conn, user):
    if authorization.is_admin(user):
        return apps_repo.list_all(conn)
    return apps_repo.list_for_owner(conn, user.id)


def _user_panel(config, conn, actor, app, app_user) -> None:
    status = "" if app_user.is_active else " · ⛔ deactivated"
    with st.expander(f"{app_user.username}{status}"):
        with st.form(f"edit_{app_user.id}"):
            email = st.text_input("Email", value=app_user.email)
            observations = st.text_area(
                "Observations", value=app_user.observations,
                help="Free notes: who requested access, approvals, context.",
            )
            if st.form_submit_button("Save"):
                aus.update_app_user(conn, app, app_user.id, email=email,
                                    observations=observations,
                                    actor=actor.username)
                st.rerun()

        columns = st.columns(3)
        if app_user.is_active:
            if columns[0].button("Deactivate", key=f"deact_{app_user.id}"):
                aus.set_app_user_active(conn, app, app_user.id, False,
                                        actor=actor.username)
                common.flash(f"'{app_user.username}' deactivated — they can no "
                             "longer log in", icon="⛔")
                st.rerun()
        else:
            if columns[0].button("Reactivate", key=f"react_{app_user.id}"):
                aus.set_app_user_active(conn, app, app_user.id, True,
                                        actor=actor.username)
                common.flash(f"'{app_user.username}' reactivated")
                st.rerun()

        confirm_key = f"confirm_del_auser_{app_user.id}"
        if st.session_state.get(confirm_key):
            st.warning(f"Really delete user '{app_user.username}' and their files?")
            if columns[1].button("Yes, delete", key=f"yesdel_{app_user.id}",
                                 type="primary"):
                aus.delete_app_user(conn, config, app, app_user.id,
                                    actor=actor.username)
                st.session_state.pop(confirm_key, None)
                common.flash(f"User '{app_user.username}' deleted")
                st.rerun()
            if columns[2].button("Cancel", key=f"canceldel_{app_user.id}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()
        elif columns[1].button("Delete", key=f"del_{app_user.id}"):
            st.session_state[confirm_key] = True
            st.rerun()

        with st.form(f"pw_{app_user.id}"):
            new_password = st.text_input("New password", type="password")
            if st.form_submit_button("Set password"):
                try:
                    aus.owner_reset_app_user_password(
                        conn, app, app_user.id, new_password, actor=actor.username
                    )
                except security.WeakPasswordError as exc:
                    st.error(str(exc))
                else:
                    st.success("Password set")

        st.markdown("**Attachments** (access evidence, screenshots, approvals)")
        for attachment in app_users_repo.list_attachments(conn, app_user.id):
            columns = st.columns([4, 1, 1])
            note = f" — {attachment.note}" if attachment.note else ""
            columns[0].caption(f"{attachment.filename}{note} "
                               f"({attachment.uploaded_at})")
            stored = layout.resolve(config, attachment.stored_path)
            if stored.exists():
                columns[1].download_button(
                    "Download", stored.read_bytes(), file_name=attachment.filename,
                    key=f"dl_{attachment.id}",
                )
            if columns[2].button("Remove", key=f"rmatt_{attachment.id}"):
                aus.delete_attachment(conn, config, app, attachment.id,
                                      actor=actor.username)
                st.rerun()
        upload = st.file_uploader("Add attachment", key=f"att_{app_user.id}")
        note = st.text_input("Attachment note", key=f"attnote_{app_user.id}",
                             placeholder="e.g. approval email screenshot")
        if upload is not None and st.button("Attach", key=f"attach_{app_user.id}"):
            aus.add_attachment(conn, config, app, app_user.id,
                               filename=upload.name, data=upload.getvalue(),
                               note=note, actor=actor.username)
            common.flash(f"'{upload.name}' attached to '{app_user.username}'")
            st.rerun()


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    st.header("App users")
    with common.open_conn(config) as conn:
        apps = _manageable_apps(conn, user)
        if not apps:
            st.info("Create an app first.")
            return
        preselect = st.session_state.pop("preselect_app_slug", None)
        index = next(
            (i for i, a in enumerate(apps) if a.slug == preselect), 0
        ) if preselect else 0
        app = st.selectbox("App", apps, index=index,
                           format_func=lambda a: f"{a.name} ({a.slug})")

        enabled = st.toggle(
            "Users Management Support",
            value=bool(app.user_mgmt_enabled),
            key=f"aum_toggle_{app.id}",
            help="When on, this app's visitors must log in with the accounts below.",
        )
        if enabled != bool(app.user_mgmt_enabled):
            app = aus.set_user_management(conn, app, enabled, actor=user.username)
        if not enabled:
            st.caption("*Login is currently not required for this app. You can "
                       "still prepare accounts below.*")

        st.subheader("Create user")
        with st.form(f"create_app_user_{app.id}", clear_on_submit=True):
            columns = st.columns(2)
            username = columns[0].text_input("Username")
            email = columns[1].text_input("Email")
            password = columns[0].text_input("Password", type="password")
            observations = columns[1].text_input("Observations")
            if st.form_submit_button("Create user"):
                try:
                    aus.create_app_user(
                        conn, app, username=username, email=email,
                        password=password, observations=observations,
                        actor=user.username,
                    )
                except (aus.AppUserError, security.WeakPasswordError,
                        AuthError) as exc:
                    st.error(str(exc))
                else:
                    common.flash(f"User '{username}' created for '{app.name}'")
                    st.rerun()

        st.subheader("Existing users")
        app_users = app_users_repo.list_for_app(conn, app.id)
        if not app_users:
            st.markdown("*No users yet.*")
        for app_user in app_users:
            _user_panel(config, conn, user, app, app_user)
