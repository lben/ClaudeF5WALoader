"""App lifecycle CLI: list | status | start | stop | restart | logs | health | reconcile."""

from __future__ import annotations

import argparse

from waloader.logging_setup import app_log_dir
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import health, lifecycle, processes, reconciliation
from waloader.tools._common import bootstrap, fail


def _get_app(conn, slug: str):
    app = apps_repo.get_by_slug(conn, slug)
    if app is None:
        raise fail(f"No app with slug '{slug}'")
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.appctl", description="Manage deployed WALoader apps"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="overview of all apps")
    for name in ("status", "start", "stop", "restart", "health"):
        cmd = sub.add_parser(name)
        cmd.add_argument("slug")
    logs = sub.add_parser("logs")
    logs.add_argument("slug")
    logs.add_argument("--kind", choices=["runtime", "deploy", "test"], default="runtime")
    logs.add_argument("--lines", type=int, default=100)
    sub.add_parser("reconcile", help="reconcile DB state with live processes")
    args = parser.parse_args(argv)

    config, conn = bootstrap()
    try:
        if args.command == "list":
            rows = reconciliation.apps_overview(conn)
            if not rows:
                print("no apps")
                return 0
            header = f"{'SLUG':24} {'STATE':18} {'PORT':6} {'VER':4} {'PROC':6} PID"
            print(header)
            for row in rows:
                print(
                    f"{row['slug']:24} {row['state']:18} {str(row['port'] or '-'):6} "
                    f"{str(row['version'] or '-'):4} {row['process']:6} "
                    f"{row['pid'] or ''}"
                )
        elif args.command == "status":
            app = _get_app(conn, args.slug)
            status = processes.app_status(conn, app)
            rt = runtime_repo.get(conn, app.id)
            print(f"app:          {app.name} ({app.slug})")
            print(f"state:        {app.state}")
            print(f"port:         {app.port}")
            print(f"version:      {app.current_version}")
            print(f"process:      {status.detail} (pid {status.pid})")
            print(f"url:          {health.app_url(config, app) if app.port else '-'}")
            if rt:
                print(f"last healthy: {rt.last_healthy_at}")
                print(f"last failure: {rt.last_failure_reason or '-'}")
            if app.last_deploy_error:
                print("last deploy error: (truncated)")
                print("  " + app.last_deploy_error.splitlines()[0][:120])
        elif args.command in ("start", "stop", "restart"):
            app = _get_app(conn, args.slug)
            operation = getattr(lifecycle, args.command)
            result = operation(conn, config, app, actor="appctl")
            print(result.message)
            return 0 if result.ok else 1
        elif args.command == "logs":
            app = _get_app(conn, args.slug)
            if app.current_version is None:
                raise fail("app has no deployed version")
            path = app_log_dir(config, app.slug, app.current_version) / f"{args.kind}.log"
            output = processes.tail_log(path, lines=args.lines)
            print(output or f"(no {args.kind}.log yet at {path})")
        elif args.command == "health":
            app = _get_app(conn, args.slug)
            alive = processes.is_app_running(conn, app)
            probe = health.probe_app(config, app, process_alive=alive)
            print(f"{app.slug}: {'HEALTHY' if probe.healthy else 'UNHEALTHY'}"
                  + (f" — {probe.reason}" if probe.reason else ""))
            return 0 if probe.healthy else 1
        elif args.command == "reconcile":
            report = reconciliation.reconcile(conn, config)
            print(f"checked {report.checked} app(s)")
            for action in report.actions:
                print(f"  {action.slug}: {action.action} ({action.detail})")
            for warning in report.warnings:
                print(f"  warning: {warning}")
            if report.resume_candidates:
                print("resume candidates: " + ", ".join(report.resume_candidates))
                print("  (start them with: appctl start <slug>)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
