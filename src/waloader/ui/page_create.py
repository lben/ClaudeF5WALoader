"""Create new app: bundle upload, live name availability, deploy + result flow."""

from __future__ import annotations

import streamlit as st

from waloader.repositories import apps as apps_repo
from waloader.services import deployment, health, slugs
from waloader.ui import common, nav


def submit_new_app(config, user, *, name, description, user_mgmt, bundle_bytes,
                   **seams):
    """Create + deploy a new app and store its outcome. Raises
    AppCreationError for pre-deploy validation problems (name taken, etc.).
    Returns (app, DeployResult). Extracted so the create flow is testable
    without driving the file-uploader widget."""
    with common.open_conn(config) as conn:
        app, result = deployment.create_app_and_deploy(
            conn, config, owner=user, name=name, description=description,
            user_mgmt_enabled=user_mgmt, bundle_bytes=bundle_bytes, **seams,
        )
        app = apps_repo.get(conn, app.id)
        common.store_deploy_outcome(app, result, health.app_url(config, app))
    return app, result


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    st.header("Create new app")
    # NB: the deploy outcome (success URL / failure+retry) is shown on the
    # DASHBOARD, never here — otherwise the create screen carries a stale
    # result from a previous deploy. Every create attempt redirects there.

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
        try:
            with st.spinner(
                f"Deploying {name.strip()}… (first deployments install "
                "dependencies and can take a few minutes)"
            ):
                app, result = submit_new_app(
                    config, user, name=name, description=description,
                    user_mgmt=user_mgmt, bundle_bytes=bundle.getvalue(),
                )
        except deployment.AppCreationError as exc:
            st.error(str(exc))  # pre-deploy validation (name taken) — fix and retry
            return
        # a create attempt always lands on the dashboard, which shows the
        # outcome (success URL, or failure + retry-upload). The create screen
        # never carries a result.
        if result.ok:
            common.flash(f"'{app.name}' deployed successfully")
        else:
            common.flash(
                f"'{app.name}' created but the deployment failed — "
                "fix and retry from the panel on the dashboard", icon="⚠️",
            )
        nav.switch("dashboard")
        st.rerun()
