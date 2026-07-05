"""App lifecycle CLI: list | status | start | stop | restart | logs | health |
reconcile | rebuild | export | import."""

from __future__ import annotations

import argparse
from pathlib import Path

from waloader.logging_setup import app_log_dir
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import (
    app_migration,
    deployment,
    health,
    lifecycle,
    processes,
    reconciliation,
)
from waloader.services.app_archive import ArchiveError
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
    rebuild = sub.add_parser(
        "rebuild", help="rebuild venv(s) from preserved bundles (after restore/import)"
    )
    rebuild.add_argument("slug", nargs="?")
    rebuild.add_argument("--all", action="store_true", dest="rebuild_all",
                         help="rebuild every app whose venv is missing")
    export = sub.add_parser("export", help="export an app to a portable archive")
    export.add_argument("slug")
    export.add_argument("--code-only", action="store_true")
    export.add_argument("--out", help="destination directory (default: backups/manual)")
    imp = sub.add_parser("import", help="import an app archive (create/un-delete)")
    imp.add_argument("archive")
    imp.add_argument("--owner", help="local username to own the app")
    imp.add_argument("--name", help="new app name (default: archived name)")
    imp.add_argument("--no-deploy", action="store_true")
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
        elif args.command == "rebuild":
            if bool(args.slug) == bool(args.rebuild_all):
                raise fail("give exactly one of: a slug, or --all")
            if args.rebuild_all:
                targets = [a for a in apps_repo.list_all(conn)
                           if deployment.needs_rebuild(config, a)]
                if not targets:
                    print("no apps need rebuilding")
                    return 0
            else:
                targets = [_get_app(conn, args.slug)]
            failed = False
            for app in targets:
                print(f"rebuilding {app.slug} (v{app.current_version}) …")
                result = deployment.rebuild_app(conn, config, app, actor_id=None)
                if result.ok:
                    print(f"  {app.slug}: running ({result.url})")
                else:
                    failed = True
                    print(f"  {app.slug}: FAILED — {result.error_summary}")
                    print("  " + "\n  ".join(result.error_block().splitlines()[:20]))
            return 1 if failed else 0
        elif args.command == "export":
            app = _get_app(conn, args.slug)
            path = app_migration.export_app(
                conn, config, app, include_data=not args.code_only,
                dest_dir=Path(args.out) if args.out else None, actor="appctl",
            )
            print(f"exported: {path}")
        elif args.command == "import":
            try:
                app, result = app_migration.import_app(
                    conn, config, Path(args.archive),
                    owner_username=args.owner, new_name=args.name,
                    deploy=not args.no_deploy, actor="appctl",
                )
            except (app_migration.ImportAppError, ArchiveError) as exc:
                raise fail(str(exc)) from exc
            print(f"imported as '{app.slug}' (owner: {args.owner or 'archived owner'})")
            if result is None:
                print("not deployed (--no-deploy): rebuild before starting: "
                      f"appctl rebuild {app.slug}")
            elif result.ok:
                print(f"running: {result.url}")
            else:
                print(f"import ok but deployment FAILED — {result.error_summary}")
                print("retry from the UI or fix and run: "
                      f"appctl rebuild {app.slug}")
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
