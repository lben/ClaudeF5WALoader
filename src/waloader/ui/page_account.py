"""Account page: profile info, password change, logout."""

from __future__ import annotations

import streamlit as st

from waloader.services import security, users_service
from waloader.ui import common


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    st.header("Account")
    st.write(f"Signed in as **{user.username}**"
             + (" (administrator)" if user.is_admin else ""))
    if user.email:
        st.caption(f"Email: {user.email}")

    st.subheader("Change password")
    with st.form("change_password"):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        confirm = st.text_input("Repeat new password", type="password")
        submitted = st.form_submit_button("Change password")
    if submitted:
        if new != confirm:
            st.error("The new passwords do not match")
        else:
            with common.open_conn(config) as conn:
                try:
                    users_service.change_password(conn, user.id, current, new)
                except (users_service.AuthError, security.WeakPasswordError) as exc:
                    st.error(str(exc))
                else:
                    st.success("Password changed")

    st.divider()
    if st.button("Log out"):
        common.logout()
