"""Health probes and (P5) the periodic health check service.

Probe layers: process alive (pid+create_time) -> TCP port open -> HTTP
`GET /<base>/_stcore/health` (Streamlit's built-in health endpoint).
"""

from __future__ import annotations

import socket
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass

from waloader.config import WALoaderConfig
from waloader.models import App


def health_url(config: WALoaderConfig, app: App) -> str:
    base = f"/apps/{app.slug}" if config.caddy.enabled else ""
    return f"http://127.0.0.1:{app.port}{base}/_stcore/health"


def app_url(config: WALoaderConfig, app: App) -> str:
    """The URL shown to users (clean Caddy URL, or direct port fallback)."""
    host = config.server.public_host
    if config.caddy.enabled:
        return f"http://{host}:{config.ports.caddy_public_port}/apps/{app.slug}"
    return f"http://{host}:{app.port}"


def port_open(port: int, *, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def probe_http(url: str, *, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


@dataclass(frozen=True)
class ProbeResult:
    healthy: bool
    reason: str  # empty when healthy


def probe_app(
    config: WALoaderConfig, app: App, *, process_alive: bool
) -> ProbeResult:
    if not process_alive:
        return ProbeResult(False, "process not running")
    if app.port is None:
        return ProbeResult(False, "no allocated port")
    if not port_open(app.port, timeout=config.health.http_timeout_seconds):
        return ProbeResult(False, f"port {app.port} not accepting connections")
    if not probe_http(health_url(config, app), timeout=config.health.http_timeout_seconds):
        return ProbeResult(False, "HTTP health endpoint not responding")
    return ProbeResult(True, "")


# --- periodic health check service ---------------------------------------


@dataclass(frozen=True)
class CheckOutcome:
    slug: str
    healthy: bool
    reason: str
    marked_failed: bool
    email_sent: bool


def check_app(
    conn: "sqlite3.Connection",
    config: WALoaderConfig,
    app: App,
    *,
    _prober=None,
) -> CheckOutcome:
    """One health check for one RUNNING app, with failure/crash handling.

    A dead process fails the app immediately; transient unhealthiness must
    repeat ``consecutive_failures_threshold`` times before the app is marked
    failed. On running -> failed, the crash notification rules run.
    """
    from waloader.notifications import service as notif_service
    from waloader.repositories import runtime as runtime_repo
    from waloader.services import processes, states

    prober = _prober or probe_app
    alive = processes.is_app_running(conn, app)
    probe = prober(config, app, process_alive=alive)
    if probe.healthy:
        runtime_repo.record_healthy(conn, app.id)
        conn.commit()
        return CheckOutcome(app.slug, True, "", False, False)

    failures = runtime_repo.record_unhealthy(conn, app.id, probe.reason)
    conn.commit()
    should_fail = (not alive) or failures >= config.health.consecutive_failures_threshold
    if not should_fail:
        return CheckOutcome(app.slug, False, probe.reason, False, False)

    email_sent = notif_service.maybe_send_crash_email(conn, config, app, probe.reason)
    states.transition(conn, app, states.FAILED)
    return CheckOutcome(app.slug, False, probe.reason, True, email_sent)


def check_all_running(
    conn: "sqlite3.Connection", config: WALoaderConfig, *, _prober=None
) -> list[CheckOutcome]:
    from waloader.repositories import apps as apps_repo
    from waloader.services import states as states_mod

    outcomes = []
    for app in apps_repo.list_all(conn):
        if app.state == states_mod.RUNNING:
            outcomes.append(check_app(conn, config, app, _prober=_prober))
    return outcomes
