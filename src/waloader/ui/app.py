"""WALoader UI entrypoint — run via `python -m waloader.tools.serve`."""

from __future__ import annotations

import streamlit as st

from waloader.services import authorization
from waloader.ui import (
    common,
    page_account,
    page_admin_backups,
    page_admin_caddy,
    page_admin_processes,
    page_admin_settings,
    page_admin_users,
    page_app_users,
    page_create,
    page_dashboard,
    page_datasets,
)

st.set_page_config(page_title="WALoader", page_icon="📦", layout="wide")

_config = common.current_config()
common.boot_once(str(_config.database_path))
_user = common.require_user(_config)

with st.sidebar:
    st.markdown(f"**WALoader** · {_user.username}"
                + (" *(admin)*" if _user.is_admin else ""))
    if st.button("Log out", key="sidebar_logout"):
        common.logout()

_sections: dict[str, list[st.Page]] = {
    "Apps": [
        st.Page(page_dashboard.render, title="Dashboard", icon="🏠",
                url_path="dashboard", default=True),
        st.Page(page_create.render, title="Create new app", icon="➕",
                url_path="create"),
        st.Page(page_datasets.render, title="Datasets", icon="🗃",
                url_path="datasets"),
        st.Page(page_app_users.render, title="App users", icon="👥",
                url_path="app-users"),
    ],
    "You": [
        st.Page(page_account.render, title="Account", icon="🔑",
                url_path="account"),
    ],
}
if authorization.is_admin(_user):
    _sections["Admin"] = [
        st.Page(page_admin_settings.render, title="Configuration", icon="⚙️",
                url_path="admin-settings"),
        st.Page(page_admin_processes.render, title="Processes", icon="📊",
                url_path="admin-processes"),
        st.Page(page_admin_caddy.render, title="Caddy", icon="🔀",
                url_path="admin-caddy"),
        st.Page(page_admin_backups.render, title="Backups & reset", icon="🗄",
                url_path="admin-backups"),
        st.Page(page_admin_users.render, title="WALoader users", icon="🛡",
                url_path="admin-users"),
    ]

st.navigation(_sections).run()
