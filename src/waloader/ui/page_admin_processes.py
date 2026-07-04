"""Admin process panel: status overview, reconciliation, resume."""

from __future__ import annotations

import streamlit as st

from waloader.services import authorization, backups, maintenance_service, reconciliation
from waloader.ui import common

SESSION_RECONCILE = "admin_reconcile_report"


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    authorization.require_admin(user)
    st.header("Processes")

    with common.open_conn(config) as conn:
        rows = reconciliation.apps_overview(conn)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("No apps.")

        _render_maintenance(config, conn)

        st.subheader("Reconciliation")
        if st.button("Run reconciliation", type="primary"):
            report = reconciliation.reconcile(conn, config)
            st.session_state[SESSION_RECONCILE] = {
                "checked": report.checked,
                "actions": [f"{a.slug}: {a.action} ({a.detail})"
                            for a in report.actions],
                "warnings": report.warnings,
                "resume_candidates": report.resume_candidates,
            }
            st.rerun()

        report = st.session_state.get(SESSION_RECONCILE)
        if not report:
            return
        st.subheader("Reconciliation result")
        st.write(f"Checked {report['checked']} app(s).")
        for action in report["actions"]:
            st.write(f"• {action}")
        for warning in report["warnings"]:
            st.warning(warning)

        candidates = report["resume_candidates"]
        if not candidates:
            st.caption("No previously-running apps to resume.")
            return
        st.subheader("Resume previously running apps")
        selected = st.multiselect("Apps to resume", candidates, default=candidates)
        columns = st.columns(2)
        clicked = None
        if columns[0].button("Resume selected", disabled=not selected):
            clicked = selected
        if columns[1].button("Resume all"):
            clicked = candidates
        if clicked:
            with st.spinner("Resuming…"):
                results = reconciliation.resume_apps(
                    conn, config, clicked, actor=user.username
                )
            for slug, result in results:
                (st.success if result.ok else st.error)(f"{slug}: {result.message}")
            st.session_state.pop(SESSION_RECONCILE, None)


def _render_maintenance(config, conn) -> None:
    st.subheader("Maintenance")
    st.caption(
        "The background worker runs these daily while WALoader is up "
        f"(health.background_enabled = {str(config.health.background_enabled).lower()}). "
        "Run them on demand here or via `python -m waloader.tools.maintenance`."
    )
    columns = st.columns(2)
    if columns[0].button("Back up database now"):
        result = backups.backup_database(config)
        (st.success if result.created else st.info)(
            result.reason + (f": {result.path}" if result.path else "")
        )
    if columns[1].button("Run full maintenance now"):
        with st.spinner("Running maintenance…"):
            report = maintenance_service.run_all(conn, config)
        st.success(report.summary())
