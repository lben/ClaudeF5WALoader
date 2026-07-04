"""Admin Caddy panel: status, generate/validate/start/stop/reload, file + logs."""

from __future__ import annotations

import streamlit as st

from waloader.services import authorization, caddy, processes
from waloader.ui import common


def render() -> None:
    config = common.current_config()
    user = common.current_user(config)
    authorization.require_admin(user)
    st.header("Caddy reverse proxy")

    with common.open_conn(config) as conn:
        info = caddy.status(conn, config)
        state = "🟢 running" if info["running"] else "⚪ not running"
        enabled = "enabled" if info["enabled"] else "disabled (direct-port mode)"
        st.markdown(f"**{state}** · {enabled} · public port "
                    f"{info['public_port']} · {info['routes']} app route(s)")
        st.caption(f"binary: `{info['binary'] or 'not found'}` · "
                   f"config: `{info['config_path']}`")
        if not info["enabled"]:
            st.info(
                "Caddy is disabled — apps are served on direct ports. Enable it "
                "under Configuration → [caddy] once a binary is configured."
            )

        columns = st.columns(5)
        result = None
        if columns[0].button("Generate"):
            path = caddy.write_caddyfile(conn, config)
            result = caddy.CaddyResult(True, f"caddyfile written: {path}")
        if columns[1].button("Validate"):
            result = caddy.validate(config)
        if columns[2].button("Start"):
            with st.spinner("Starting caddy…"):
                result = caddy.start(conn, config)
        if columns[3].button("Stop"):
            result = caddy.stop(config)
        if columns[4].button("Reload"):
            result = caddy.reload(conn, config)
        if result is not None:
            (st.success if result.ok else st.error)(result.output or "ok")

        if info["config_exists"]:
            with st.expander("Current generated Caddyfile"):
                st.code(config.caddy_config_path.read_text(encoding="utf-8"),
                        language=None)

        with st.expander("Caddy logs"):
            for name in ("caddy.log", "access.log"):
                path = config.caddy_logs_dir / name
                st.caption(str(path))
                tail = processes.tail_log(path, lines=100)
                st.code(tail or "(empty)", language=None)
