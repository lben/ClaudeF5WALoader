"""Port allocation: DB reservation + real socket check, atomic, stable reuse."""

from __future__ import annotations

import socket
import sqlite3

from waloader.config import WALoaderConfig
from waloader.db import transaction
from waloader.repositories import apps as apps_repo


class PortAllocationError(Exception):
    pass


def port_is_free(port: int) -> bool:
    """Bind test on all interfaces — the authoritative 'actually available' check."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("", port))
            return True
        except OSError:
            return False


def allocate_port(conn: sqlite3.Connection, config: WALoaderConfig, app_id: int) -> int:
    """Reserve a port for the app inside one immediate transaction.

    Keeps the app's existing port when it is still in range and free (stable
    URLs across restarts); otherwise takes the first port that is neither
    reserved in the DB nor actually occupied on this host.
    """
    from waloader.services import processes  # local import; avoids a cycle

    start, end = config.ports.child_app_start, config.ports.child_app_end
    with transaction(conn):
        app = apps_repo.get(conn, app_id)
        if app.port is not None and start <= app.port <= end:
            # Keep the port if it's free OR occupied by this app's own process
            # (update flow: the old version is stopped before the new launch).
            if port_is_free(app.port) or processes.is_app_running(conn, app):
                return app.port
        used = apps_repo.used_ports(conn) - ({app.port} if app.port else set())
        for candidate in range(start, end + 1):
            if candidate in used:
                continue
            if not port_is_free(candidate):
                continue
            apps_repo.set_port(conn, app_id, candidate)
            return candidate
    raise PortAllocationError(
        f"No free port available in the configured range {start}-{end}. "
        "Widen ports.child_app_start/child_app_end or stop unused apps."
    )


def release_port(conn: sqlite3.Connection, app_id: int) -> None:
    apps_repo.set_port(conn, app_id, None)
    conn.commit()
