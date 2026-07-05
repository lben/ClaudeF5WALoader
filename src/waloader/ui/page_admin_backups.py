"""Admin: Backups & reset — thin client of scoped_backups / app_migration /
factory_reset (the same services backupctl wraps)."""

from __future__ import annotations

import streamlit as st

from waloader.paths import ensure_dir
from waloader.repositories import users as users_repo
from waloader.services import app_migration, authorization
from waloader.services import factory_reset as frs
from waloader.services import scoped_backups as sb
from waloader.ui import common

SCOPE_CHOICES = {
    "Everything (full instance)": "all",
    "Database only (admin data)": "db",
    "All apps": "apps",
    "One app": "app",
}


def _create_section(config, conn) -> None:
    st.subheader("Create backup")
    label = st.radio("Scope", list(SCOPE_CHOICES), key="backup_scope")
    scope = SCOPE_CHOICES[label]
    app_slug = None
    include_data = True
    include_logs = False
    if scope == "app":
        from waloader.repositories import apps as apps_repo

        apps = apps_repo.list_all(conn)
        if not apps:
            st.info("No apps to back up.")
            return
        app_slug = st.selectbox(
            "App", [a.slug for a in apps], key="backup_app_slug"
        )
    if scope in ("apps", "app"):
        include_data = not st.toggle(
            "Code only (exclude datasets and user files)", key="backup_code_only"
        )
    if scope == "all":
        include_logs = st.toggle("Include logs", key="backup_with_logs")

    if st.button("Create backup", type="primary", key="backup_create"):
        with st.spinner("Creating backup…"):
            try:
                result = sb.create_backup(
                    conn, config, scope, app_slug=app_slug,
                    include_data=include_data, include_logs=include_logs,
                    actor="admin-panel",
                )
            except sb.BackupError as exc:
                st.error(str(exc))
                return
        st.session_state["last_backup"] = str(result.path)
        st.rerun()
    last = st.session_state.get("last_backup")
    if last:
        from pathlib import Path

        path = Path(last)
        if path.exists():
            st.success(f"Backup created: `{path.name}`")
            st.download_button("Download it", path.read_bytes(),
                               file_name=path.name, key="dl_last_backup")


def _existing_section(config) -> None:
    st.subheader("Existing backups")
    infos = sb.list_backups(config)
    if not infos:
        st.caption("*No backups yet.*")
        return
    for info in infos:
        with st.container(border=True):
            columns = st.columns([4, 1, 1])
            purge = f" · purge {info.purge_after[:10]}" if info.purge_after else ""
            columns[0].markdown(
                f"**{info.name}**  \n{info.kind} · {info.scope} · "
                f"{info.size_bytes:,} bytes · {info.created_at}{purge}"
            )
            if info.path.exists():
                columns[1].download_button(
                    "Download", info.path.read_bytes(), file_name=info.name,
                    key=f"dl_{info.name}",
                )
            confirm_key = f"confirm_del_backup_{info.name}"
            if st.session_state.get(confirm_key):
                st.warning(f"Delete backup '{info.name}' permanently?")
                yes, no = st.columns(2)
                if yes.button("Yes, delete", key=f"yes_{info.name}", type="primary"):
                    sb.delete_backup(config, info.name)
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
                if no.button("Cancel", key=f"no_{info.name}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
            elif columns[2].button("Delete", key=f"del_{info.name}"):
                st.session_state[confirm_key] = True
                st.rerun()


def _import_section(config, conn, user) -> None:
    st.subheader("Import app")
    st.caption(
        "Bring an exported app (or a soft-delete archive) into this instance. "
        "The app is rebuilt and started unless you untick Deploy."
    )
    upload = st.file_uploader("App archive (.zip)", type=["zip"], key="import_zip")
    columns = st.columns(3)
    owners = users_repo.list_all(conn)
    owner_name = columns[0].selectbox(
        "Owner", [u.username for u in owners], key="import_owner"
    )
    new_name = columns[1].text_input("New name (optional)", key="import_name")
    deploy = columns[2].toggle("Deploy now", value=True, key="import_deploy")
    if upload is not None and st.button("Import", type="primary", key="import_go"):
        staging = ensure_dir(config.tmp_dir) / f"import-{upload.name}"
        staging.write_bytes(upload.getvalue())
        try:
            with st.spinner("Importing…"):
                app, result = app_migration.import_app(
                    conn, config, staging,
                    owner_username=owner_name,
                    new_name=new_name.strip() or None,
                    deploy=deploy, actor=user.username,
                )
        except (app_migration.ImportAppError, Exception) as exc:  # noqa: BLE001
            st.error(str(exc))
            return
        finally:
            staging.unlink(missing_ok=True)
        if result is None:
            st.success(
                f"Imported as '{app.slug}' (not deployed). Rebuild it from the "
                "app's gear dialog before starting."
            )
        else:
            from waloader.services import health

            common.store_deploy_outcome(app, result, health.app_url(config, app))
            st.rerun()


def _danger_zone(config) -> None:
    st.subheader("Danger zone — Factory reset")
    with st.container(border=True):
        st.error(
            "Factory reset stops every app, takes a full backup (kept "
            f"{config.retention.factory_reset_backup_days} days under "
            "`backups/factory/`), then wipes the data directory back to "
            "first-run. Only `backups/` survives."
        )
        typed = st.text_input(
            "Type RESET to enable the button", key="reset_confirm_text"
        )
        clicked = st.button(
            "Factory reset this WALoader",
            type="primary",
            disabled=typed.strip() != "RESET",
            key="factory_reset_go",
        )
        if clicked and typed.strip() == "RESET":  # server-side gate, not just UI
            with st.spinner("Backing up and resetting…"):
                report = frs.factory_reset(config, actor="admin-panel")
            st.session_state["factory_reset_report"] = {
                "summary": report.summary(),
                "notes": report.notes,
            }
            st.rerun()


def render() -> None:
    # After a reset the DB is GONE: show the report and stop BEFORE any code
    # path that would open (and thereby recreate) a database file.
    if st.session_state.get("factory_reset_report"):
        report = st.session_state["factory_reset_report"]
        st.header("Backups & reset")
        st.success(report["summary"])
        for note in report["notes"]:
            st.warning(note)
        st.stop()

    config = common.current_config()
    user = common.current_user(config)
    authorization.require_admin(user)
    st.header("Backups & reset")

    with common.open_conn(config) as conn:
        _create_section(config, conn)
        _existing_section(config)
        _import_section(config, conn, user)
    _danger_zone(config)
