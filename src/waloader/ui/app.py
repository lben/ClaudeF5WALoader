"""WALoader UI entrypoint — run via `python -m waloader.tools.serve`."""

from __future__ import annotations

import streamlit as st

from waloader.ui import common, page_account, page_create, page_dashboard

st.set_page_config(page_title="WALoader", page_icon="📦", layout="wide")

_config = common.current_config()
common.boot_once(str(_config.database_path))
_user = common.require_user(_config)

with st.sidebar:
    st.markdown(f"**WALoader** · {_user.username}"
                + (" *(admin)*" if _user.is_admin else ""))
    if st.button("Log out", key="sidebar_logout"):
        common.logout()

_pages = [
    st.Page(page_dashboard.render, title="Dashboard", icon="🏠",
            url_path="dashboard", default=True),
    st.Page(page_create.render, title="Create new app", icon="➕",
            url_path="create"),
    st.Page(page_account.render, title="Account", icon="🔑", url_path="account"),
]

st.navigation(_pages).run()
