"""Admin configuration panel: every setting with effective value + source.

Edits are stored as DB overrides (settings table) on top of the TOML file;
[paths] is bootstrap-only and shown read-only with the derived layout.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from waloader.config import (
    ConfigError,
    LoadedConfig,
    WALoaderConfig,
    apply_db_overrides,
    is_db_editable,
    load_config,
)
from waloader.repositories import audit as audit_repo
from waloader.repositories import settings as settings_repo
from waloader.services import authorization
from waloader.ui import common

EDITABLE_SECTIONS = [
    "server", "executables", "uv", "ports", "caddy", "dependencies_policy",
    "uploads", "health", "notifications", "retention", "database", "debug", "apps",
]


def _loaded_with_overrides(conn) -> LoadedConfig:
    loaded = load_config()
    return apply_db_overrides(loaded, settings_repo.get_all(conn))


def _widget_for(dotted: str, value: Any, source: str, default: Any):
    key = f"set_{dotted}"
    help_text = f"source: {source} · default: {default!r}"
    if isinstance(value, bool):
        return st.toggle(dotted, value=value, key=key, help=help_text)
    if isinstance(value, int):
        return st.number_input(dotted, value=value, key=key, help=help_text, step=1)
    if isinstance(value, list):
        raw = st.text_input(f"{dotted} (JSON list)", value=json.dumps(value),
                            key=key, help=help_text)
        return raw
    return st.text_input(dotted, value=value, key=key, help=help_text)


def _collect(dotted: str, current: Any) -> Any:
    raw = st.session_state.get(f"set_{dotted}", current)
    if isinstance(current, list):
        return json.loads(raw)  # caller catches errors
    if isinstance(current, bool):
        return bool(raw)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    return raw


def _render_section(conn, loaded: LoadedConfig, section: str) -> None:
    defaults = WALoaderConfig()
    section_data = loaded.config.model_dump()[section]
    default_data = defaults.model_dump()[section]
    has_override = any(
        loaded.source_of(f"{section}.{key}") == "db" for key in section_data
    )
    suffix = " · has DB overrides" if has_override else ""
    with st.expander(f"[{section}]{suffix}"):
        with st.form(f"form_{section}"):
            for key, value in section_data.items():
                dotted = f"{section}.{key}"
                _widget_for(dotted, value, loaded.source_of(dotted),
                            default_data[key])
            saved = st.form_submit_button("Save")
        if saved:
            changes: dict[str, Any] = {}
            try:
                for key, value in section_data.items():
                    dotted = f"{section}.{key}"
                    new_value = _collect(dotted, value)
                    if new_value != value:
                        changes[dotted] = new_value
            except (json.JSONDecodeError, ValueError) as exc:
                st.error(f"Invalid value: {exc}")
                return
            if not changes:
                st.info("No changes")
                return
            try:  # validate the full overlay before persisting anything
                overrides = settings_repo.get_all(conn) | changes
                apply_db_overrides(load_config(), overrides)
            except ConfigError as exc:
                st.error(str(exc))
                return
            for dotted, value in changes.items():
                if is_db_editable(dotted):
                    settings_repo.set_value(conn, dotted, value)
            audit_repo.record(conn, actor="admin-panel", action="settings.update",
                              target=",".join(changes))
            conn.commit()
            st.success(f"Saved: {', '.join(changes)}")
            st.rerun()
        if has_override and st.button(
            f"Clear DB overrides for [{section}]", key=f"clear_{section}"
        ):
            for key in section_data:
                settings_repo.delete(conn, f"{section}.{key}")
            conn.commit()
            st.rerun()


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    authorization.require_admin(user)
    st.header("Configuration")
    st.caption(
        "Effective value = defaults ← config file ← these DB overrides. "
        "Port/Caddy/binary changes affect new operations; restart `serve` (and "
        "Caddy) for the WALoader port or baseUrlPath to change."
    )

    with common.open_conn(config) as conn:
        loaded = _loaded_with_overrides(conn)

        with st.expander("[paths] (read-only — set in the config file)"):
            st.caption(
                f"config file: `{loaded.config_path or 'none (defaults)'}`"
            )
            rows = [
                {"path": name, "location": str(value)}
                for name, value in loaded.config.derived_paths().items()
            ]
            st.table(rows)

        for section in EDITABLE_SECTIONS:
            _render_section(conn, loaded, section)
