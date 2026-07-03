"""Health probes and (P5) the periodic health check service.

Probe layers: process alive (pid+create_time) -> TCP port open -> HTTP
`GET /<base>/_stcore/health` (Streamlit's built-in health endpoint).
"""

from __future__ import annotations

import socket
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
