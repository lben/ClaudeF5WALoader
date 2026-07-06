"""Create new app: bundle upload, live name availability, deploy + result flow."""

from __future__ import annotations

import streamlit as st

from waloader.repositories import apps as apps_repo
from waloader.services import deployment, health, slugs
from waloader.ui import common, nav


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    st.header("Create new app")
    common.render_deploy_outcome(config, user)

    name = st.text_input("App name", placeholder="e.g. Client Positions Dashboard")
    availability = None
    if name.strip():
        with common.open_conn(config) as conn:
            availability = slugs.check_name_available(conn, name)
        if availability.available:
            st.markdown(f"✅ Available — URL name: `{availability.slug}`")
        else:
            st.markdown("❌ Not available")
            st.caption(f"*{availability.reason}*")

    description = st.text_area("Description", placeholder="What does this app do?")
    user_mgmt = st.toggle(
        "Users Management Support",
        value=False,
        help="Require this app's users to log in before using it. You manage "
             "those users yourself after the app is created.",
    )
    st.caption(
        "*Dataset Concepts can be defined right after the app is created "
        "(gear icon → Datasets).*"
    )
    bundle = st.file_uploader(
        "Markdown project bundle",
        type=["md", "txt", "markdown"],
        help="The single markdown file your coding LLM generated "
             "(see the bundle contract in the docs).",
    )

    can_submit = bool(
        name.strip() and availability and availability.available and bundle is not None
    )
    if st.button("Create app", type="primary", disabled=not can_submit):
        with common.open_conn(config) as conn:
            with st.spinner(
                f"Deploying {name.strip()}… (first deployments install "
                "dependencies and can take a few minutes)"
            ):
                try:
                    app, result = deployment.create_app_and_deploy(
                        conn, config,
                        owner=user,
                        name=name,
                        description=description,
                        user_mgmt_enabled=user_mgmt,
                        bundle_bytes=bundle.getvalue(),
                    )
                except deployment.AppCreationError as exc:
                    st.error(str(exc))
                    return
            app = apps_repo.get(conn, app.id)
            common.store_deploy_outcome(app, result, health.app_url(config, app))
        if result.ok:
            # land on the dashboard with the success panel — staying on the
            # (now reset) create form was confusing in field testing
            common.flash(f"'{app.name}' deployed successfully")
            nav.switch("dashboard")
        st.rerun()
