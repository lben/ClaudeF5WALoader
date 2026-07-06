"""Launch the WALoader UI with the correct flags derived from config.

    uv run python -m waloader.tools.serve             # foreground (Ctrl+C stops)
    uv run python -m waloader.tools.serve --daemon    # detached: survives logout
    uv run python -m waloader.tools.serve --status
    uv run python -m waloader.tools.serve --stop

Foreground mode runs migrations, reconciles state, then execs Streamlit.
Daemon mode spawns the same Streamlit process detached (its own session, no
controlling terminal — immune to SIGHUP when your SSH session drops), logs to
data/logs/waloader/serve.log, and tracks it via a pidfile with the process
creation time (PID reuse never matches). Note for locked-down RHEL boxes: if
systemd-logind is configured with KillUserProcesses=yes, ask the admin for
``loginctl enable-linger <you>`` — otherwise logind kills even detached user
processes at logout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import waloader
from waloader.config import ConfigError, WALoaderConfig, load_config
from waloader.services import processes, reconciliation
from waloader.tools._common import bootstrap

UI_ENTRYPOINT = Path(waloader.__file__).parent / "ui" / "app.py"


def build_serve_command(config: WALoaderConfig) -> list[str]:
    command = [
        sys.executable, "-m", "streamlit", "run", str(UI_ENTRYPOINT),
        "--server.port", str(config.ports.waloader_port),
        "--server.address", "127.0.0.1" if config.caddy.enabled else "0.0.0.0",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.maxUploadSize", str(
            max(config.uploads.max_markdown_bundle_mb, config.uploads.max_dataset_file_mb)
        ),
    ]
    if config.caddy.enabled:
        command += ["--server.baseUrlPath", "waloader"]
    return command


def _ui_url(config: WALoaderConfig) -> str:
    if config.caddy.enabled:
        return f"http://{config.server.public_host}:{config.ports.caddy_public_port}/waloader"
    return f"http://{config.server.public_host}:{config.ports.waloader_port}"


# --- daemon management (pidfile with create_time, like the caddy wrapper) ----


def _pidfile(config: WALoaderConfig) -> Path:
    return config.data_dir / "waloader.pid.json"


def _read_pidfile(config: WALoaderConfig) -> tuple[int, float] | None:
    path = _pidfile(config)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data["pid"]), float(data["create_time"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def daemon_running(config: WALoaderConfig) -> tuple[bool, int | None]:
    record = _read_pidfile(config)
    if record is None or not processes.pid_matches(*record):
        return False, None
    return True, record[0]


def start_daemon(config: WALoaderConfig) -> tuple[bool, str]:
    running, pid = daemon_running(config)
    if running:
        return False, f"WALoader is already running as a daemon (pid {pid})."
    log_file = config.logs_dir / "waloader" / "serve.log"
    pid, create_time = processes.spawn_detached(
        build_serve_command(config),
        cwd=Path.cwd(),
        env=dict(os.environ),
        log_file=log_file,
    )
    _pidfile(config).parent.mkdir(parents=True, exist_ok=True)
    _pidfile(config).write_text(
        json.dumps({"pid": pid, "create_time": create_time}), encoding="utf-8"
    )
    return True, (
        f"WALoader daemon started (pid {pid}).\n"
        f"  UI:   {_ui_url(config)}\n"
        f"  logs: {log_file}\n"
        "  stop: uv run python -m waloader.tools.serve --stop"
    )


def stop_daemon(config: WALoaderConfig) -> str:
    record = _read_pidfile(config)
    if record is None or not processes.pid_matches(*record):
        _pidfile(config).unlink(missing_ok=True)
        return "WALoader daemon is not running (child apps keep running regardless)."
    processes.terminate_tree(record[0], record[1], timeout=15)
    _pidfile(config).unlink(missing_ok=True)
    return (
        f"WALoader daemon stopped (pid {record[0]}). Child apps keep running "
        "detached; stop them individually with appctl if needed."
    )


def _light_config() -> WALoaderConfig:
    """Config without DB migrations/reconcile — for --status/--stop."""
    try:
        return load_config().config
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.serve", description="Run the WALoader UI"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true",
                      help="run detached; survives SSH logout")
    mode.add_argument("--stop", action="store_true", help="stop the daemon")
    mode.add_argument("--status", action="store_true", help="daemon status")
    mode.add_argument("--print-command", action="store_true",
                      help="show the streamlit command without launching")
    args = parser.parse_args(argv)

    if args.status:
        config = _light_config()
        running, pid = daemon_running(config)
        if running:
            print(f"WALoader daemon: running (pid {pid}) — {_ui_url(config)}")
        else:
            print("WALoader daemon: not running")
        return 0
    if args.stop:
        print(stop_daemon(_light_config()))
        return 0

    config, conn = bootstrap(console_logging=not args.daemon)
    try:
        report = reconciliation.reconcile(conn, config)
        if report.resume_candidates:
            print(
                "reconcile: previously-running apps found stopped: "
                + ", ".join(report.resume_candidates)
                + "  (resume from the admin panel or appctl start)"
            )
    finally:
        conn.close()

    if args.daemon:
        ok, message = start_daemon(config)
        print(message)
        return 0 if ok else 1

    command = build_serve_command(config)
    print(f"WALoader UI: {_ui_url(config)}")
    if args.print_command:
        print(" ".join(command))
        return 0
    return subprocess.call(command)  # noqa: S603 - argv list, no shell


if __name__ == "__main__":
    raise SystemExit(main())
