"""Startup reconciliation: bring DB state and reality back in line.

Run at WALoader startup (and on demand from the admin panel / appctl). Apps
that the DB believes are running but whose processes are gone become
``stopped`` and are reported as resume candidates — never ``failed``, so a
WALoader restart can never trigger crash emails.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from waloader.config import WALoaderConfig
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import lifecycle, ports, processes, states

log = logging.getLogger(__name__)


@dataclass
class ReconcileAction:
    slug: str
    action: str
    detail: str = ""


@dataclass
class ReconcileReport:
    checked: int = 0
    actions: list[ReconcileAction] = field(default_factory=list)
    resume_candidates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def reconcile(conn: sqlite3.Connection, config: WALoaderConfig) -> ReconcileReport:
    report = ReconcileReport()
    for app in apps_repo.list_all(conn):
        report.checked += 1
        alive = processes.is_app_running(conn, app)

        if app.state in (states.RUNNING, states.DEPLOYING) and not alive:
            runtime_repo.clear_process(conn, app.id)
            states.transition(conn, app, states.STOPPED)
            report.actions.append(
                ReconcileAction(app.slug, "marked stopped",
                                f"was {app.state} but its process is gone")
            )
            report.resume_candidates.append(app.slug)
            if app.port is not None and not ports.port_is_free(app.port):
                report.warnings.append(
                    f"'{app.slug}': its port {app.port} is now occupied by another "
                    "process; resuming will allocate a new port"
                )
        elif app.state in (states.STOPPED, states.FAILED) and alive:
            states.transition(conn, app, states.RUNNING)
            report.actions.append(
                ReconcileAction(app.slug, "adopted running process",
                                f"was {app.state} but its recorded process is alive")
            )
        elif app.state == states.RUNNING and alive:
            pass  # healthy bookkeeping
    audit_repo.record(
        conn, actor="reconcile", action="reconcile.run", target="",
        details={
            "checked": report.checked,
            "actions": [f"{a.slug}: {a.action}" for a in report.actions],
        },
    )
    conn.commit()
    log.info(
        "reconcile: %d checked, %d fixed, %d resume candidate(s)",
        report.checked, len(report.actions), len(report.resume_candidates),
    )
    return report


def resume_apps(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    slugs: list[str],
    *,
    actor: str = "",
    _launcher=processes.start_app,
    _prober=None,
) -> list[tuple[str, lifecycle.LifecycleResult]]:
    """Admin-only: start previously-running apps found by reconcile."""
    from waloader.services import health

    prober = _prober or health.probe_app
    results = []
    for slug in slugs:
        app = apps_repo.get_by_slug(conn, slug)
        if app is None:
            results.append(
                (slug, lifecycle.LifecycleResult(False, f"no app with slug '{slug}'"))
            )
            continue
        results.append(
            (slug, lifecycle.start(conn, config, app, actor=actor,
                                   _launcher=_launcher, _prober=prober))
        )
    return results


def apps_overview(conn: sqlite3.Connection) -> list[dict]:
    """Status snapshot for the admin process panel and appctl list."""
    rows = []
    for app in apps_repo.list_all(conn):
        rt = runtime_repo.get(conn, app.id)
        status = processes.app_status(conn, app)
        rows.append(
            {
                "slug": app.slug,
                "name": app.name,
                "state": app.state,
                "port": app.port,
                "version": app.current_version,
                "process": "alive" if status.running else "-",
                "pid": status.pid if status.running else None,
                "last_healthy": rt.last_healthy_at if rt else None,
                "last_failure": rt.last_failure_reason if rt else None,
            }
        )
    return rows
