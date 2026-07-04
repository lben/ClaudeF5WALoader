"""Admin-only: manage WALoader platform users."""

from __future__ import annotations

import streamlit as st

from waloader.repositories import users as users_repo
from waloader.services import authorization, security, users_service
from waloader.ui import common


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    authorization.require_admin(user)
    st.header("WALoader users")

    with common.open_conn(config) as conn:
        st.subheader("Create user")
        with st.form("create_platform_user", clear_on_submit=True):
            columns = st.columns(2)
            username = columns[0].text_input("Username")
            email = columns[1].text_input("Email")
            password = columns[0].text_input("Password", type="password")
            is_admin = columns[1].checkbox("Administrator")
            if st.form_submit_button("Create user"):
                try:
                    users_service.create_user(
                        conn, username=username, email=email, password=password,
                        is_admin=is_admin, actor=user.username,
                    )
                except (users_service.UserValidationError,
                        security.WeakPasswordError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

        st.subheader("Existing users")
        for other in users_repo.list_all(conn):
            role = "admin" if other.is_admin else "user"
            state = "" if other.is_active else " · ⛔ deactivated"
            with st.expander(f"{other.username} ({role}){state}"):
                st.caption(f"{other.email or 'no email'} · created {other.created_at}")
                columns = st.columns(3)
                if other.id != user.id:
                    if other.is_active:
                        if columns[0].button("Deactivate", key=f"pdeact_{other.id}"):
                            users_service.set_active(conn, other.id, False,
                                                     actor=user.username)
                            st.rerun()
                    elif columns[0].button("Reactivate", key=f"preact_{other.id}"):
                        users_service.set_active(conn, other.id, True,
                                                 actor=user.username)
                        st.rerun()
                else:
                    columns[0].caption("(you)")
                with st.form(f"ppw_{other.id}"):
                    new_password = st.text_input("New password", type="password")
                    if st.form_submit_button("Set password"):
                        try:
                            users_service.admin_reset_password(
                                conn, other.id, new_password, actor=user.username
                            )
                        except security.WeakPasswordError as exc:
                            st.error(str(exc))
                        else:
                            st.success("Password set")
