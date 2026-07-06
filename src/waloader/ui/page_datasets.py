"""Dataset Concepts mapping screen (G01 §4.15)."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from waloader.repositories import apps as apps_repo
from waloader.services import authorization, datasets_service, layout
from waloader.ui import common

EXCEL_EXTENSIONS = {".xlsx", ".xls"}


def _manageable_apps(conn, user):
    if authorization.is_admin(user):
        return apps_repo.list_all(conn)
    return apps_repo.list_for_owner(conn, user.id)


def _concept_row(config, conn, user, app, concept, current) -> None:
    with st.container(border=True):
        head, delete_col = st.columns([5, 1])
        head.markdown(f"**`{concept.name}`**")
        confirm_key = f"confirm_del_concept_{concept.id}"
        if st.session_state.get(confirm_key):
            st.warning(f"Delete concept '{concept.name}' and its uploaded data?")
            yes, no = st.columns(2)
            if yes.button("Yes, delete", key=f"yes_del_{concept.id}", type="primary"):
                datasets_service.delete_concept(conn, config, app, concept.id,
                                                actor=user.username)
                st.session_state.pop(confirm_key, None)
                common.flash(f"Concept '{concept.name}' and its data deleted")
                st.rerun()
            if no.button("Cancel", key=f"no_del_{concept.id}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()
            return
        if delete_col.button("Delete", key=f"del_concept_{concept.id}"):
            st.session_state[confirm_key] = True
            st.rerun()

        if current is None:
            st.markdown("*No data uploaded yet*")
        else:
            sheet_note = f", sheet `{current.sheet_name}`" if current.sheet_name else ""
            st.caption(
                f"current: {current.original_filename}{sheet_note} · "
                f"{current.size_bytes:,} bytes · uploaded {current.uploaded_at}"
            )
            with st.expander("Current schema"):
                st.code(json.dumps(current.schema, indent=2), language="json")

        uploaded = st.file_uploader(
            "Upload data file" if current is None else "Upload replacement file",
            type=[e.lstrip(".") for e in config.uploads.allowed_dataset_extensions],
            key=f"upload_{concept.id}",
        )
        if uploaded is None:
            return

        extension = Path(uploaded.name).suffix.lower()
        sheet_name = None
        if extension in EXCEL_EXTENSIONS:
            sheet_name = st.text_input(
                "Excel sheet name (required)",
                value=config.uploads.default_excel_sheet_name,
                key=f"sheet_{concept.id}",
            )
            if not sheet_name.strip():
                st.error("A sheet name is required for Excel files")
                return

        data = uploaded.getvalue()
        if current is None:
            if st.button("Upload", key=f"store_{concept.id}", type="primary"):
                try:
                    datasets_service.store_upload(
                        conn, config, app, concept, filename=uploaded.name,
                        data=data, sheet_name=sheet_name,
                        uploaded_by=user.id, actor=user.username,
                    )
                except datasets_service.DatasetError as exc:
                    st.error(str(exc))
                    return
                common.flash(
                    f"'{uploaded.name}' uploaded for '{concept.name}' — the app "
                    "can use it now"
                )
                st.rerun()
            return

        # replacement: show the schema diff and require confirmation on changes
        try:
            diff = datasets_service.replacement_diff(
                conn, config, concept, uploaded.name, data, sheet_name
            )
        except datasets_service.DatasetError as exc:
            st.error(str(exc))
            return
        if diff.has_changes:
            st.warning("The replacement's schema differs from the current data:")
            st.code(diff.format(), language=None)
            st.caption(
                "*A mismatch can be fine if you also updated the app code to "
                "use the new schema.*"
            )
            label = "Replace anyway — I checked the app code"
        else:
            st.caption("Schema matches the current data.")
            label = "Replace"
        if st.button(label, key=f"replace_{concept.id}", type="primary"):
            try:
                datasets_service.store_upload(
                    conn, config, app, concept, filename=uploaded.name,
                    data=data, sheet_name=sheet_name,
                    uploaded_by=user.id, actor=user.username,
                )
            except datasets_service.DatasetError as exc:
                st.error(str(exc))
                return
            common.flash(
                f"'{concept.name}' replaced with '{uploaded.name}'"
            )
            st.rerun()


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    st.header("Dataset Concepts")
    st.caption(
        "Concepts are the stable names your app code loads with "
        "`load_dataset(\"<concept>\")`. Upload a file per concept; replacements "
        "are schema-checked before overwriting."
    )

    with common.open_conn(config) as conn:
        apps = _manageable_apps(conn, user)
        if not apps:
            st.info("Create an app first — then map its Dataset Concepts here.")
            return
        preselect = st.session_state.pop("preselect_app_slug", None)
        index = next(
            (i for i, a in enumerate(apps) if a.slug == preselect), 0
        ) if preselect else 0
        app = st.selectbox(
            "App", apps, index=index, format_func=lambda a: f"{a.name} ({a.slug})"
        )

        with st.form("add_concept", clear_on_submit=True):
            columns = st.columns([4, 1])
            new_name = columns[0].text_input(
                "New concept name", placeholder="e.g. clients, transactions"
            )
            submitted = columns[1].form_submit_button("Save")
        if submitted and new_name.strip():
            try:
                datasets_service.create_concept(conn, app, new_name,
                                                actor=user.username)
            except datasets_service.DatasetError as exc:
                st.error(str(exc))
            else:
                common.flash(f"Concept '{new_name.strip()}' saved — upload its "
                             "data file below")
                st.rerun()

        pairs = datasets_service.list_concepts_with_files(conn, app)
        if not pairs:
            st.markdown("*No concepts defined yet.*")
            st.caption(
                f"Data files live under `{layout.datasets_dir(config, app.slug)}`."
            )
            return
        for concept, current in pairs:
            _concept_row(config, conn, user, app, concept, current)
