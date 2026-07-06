"""Registry of st.Page objects so dialogs and pages can switch pages.

st.switch_page needs the exact Page instance registered with st.navigation;
app.py registers each page here every rerun. Contexts without navigation
(AppTest page runs) fall back to a plain rerun.
"""

from __future__ import annotations

import streamlit as st

_PAGES: dict[str, object] = {}


def register(key: str, page):
    _PAGES[key] = page
    return page


def switch(key: str) -> None:
    page = _PAGES.get(key)
    if page is not None:
        st.switch_page(page)
    else:  # bare/AppTest context: no navigation to switch within
        st.rerun()
