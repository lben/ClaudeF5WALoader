"""App lifecycle operations: start (resume), stop, restart.

Shared verbatim by the dashboard UI, the appctl CLI, and reconciliation's
resume feature. Deployment has its own pipeline; these operate on the app's
already-deployed current version.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import versions as versions_repo
from waloader.services import caddy, health, ports, processes, states


@dataclass
class LifecycleResult:
    ok: bool
    message: str


def start(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    *,
    actor: str = "",
    _launcher=processes.start_app,
    _prober=health.probe_app,
) -> LifecycleResult:
    """Start (resume) a stopped/failed app on its current version."""
    if processes.is_app_running(conn, app):
        return LifecycleResult(True, f"'{app.slug}' is already running")
    if app.current_version is None:
        return LifecycleResult(
            False, f"'{app.slug}' has no deployed version yet — upload a bundle first"
        )
    version = versions_repo.get_by_number(conn, app.id, app.current_version)
    if version is None:
        return LifecycleResult(
            False, f"Version {app.current_version} of '{app.slug}' is missing"
        )
    try:
        old_port = app.port
        ports.allocate_port(conn, config, app.id)  # revalidates the stored port
        app = apps_repo.get(conn, app.id)
    except ports.PortAllocationError as exc:
        return LifecycleResult(False, str(exc))

    launched = _launcher(conn, config, app, version)
    deadline = time.monotonic() + config.health.initial_check_timeout_seconds
    probe = None
    while time.monotonic() < deadline:
        alive = processes.pid_matches(*launched)
        probe = _prober(config, app, process_alive=alive)
        if probe.healthy or not alive:
            break
        time.sleep(0.5)
    if probe is None or not probe.healthy:
        reason = probe.reason if probe else "no probe ran"
        processes.stop_app(conn, config, app)
        return LifecycleResult(False, f"'{app.slug}' failed to start: {reason}")

    states.transition(conn, app, states.RUNNING)
    if config.caddy.enabled and app.port != old_port:
        caddy.refresh_routes(conn, config)
    audit_repo.record(conn, actor=actor, action="app.start", target=app.slug)
    conn.commit()
    return LifecycleResult(True, f"'{app.slug}' is running on port {app.port}")


def stop(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, *, actor: str = ""
) -> LifecycleResult:
    """User/operator-triggered stop. Never produces a crash email (state ->
    stopped, not failed)."""
    processes.stop_app(conn, config, app)
    if app.state != states.STOPPED:
        states.transition(conn, app, states.STOPPED)
    audit_repo.record(conn, actor=actor, action="app.stop", target=app.slug)
    conn.commit()
    return LifecycleResult(True, f"'{app.slug}' stopped")


def restart(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    *,
    actor: str = "",
    _launcher=processes.start_app,
    _prober=health.probe_app,
) -> LifecycleResult:
    stop(conn, config, app, actor=actor)
    app = apps_repo.get(conn, app.id)
    result = start(conn, config, app, actor=actor, _launcher=_launcher, _prober=_prober)
    if result.ok:
        return LifecycleResult(True, f"'{app.slug}' restarted")
    return result
