"""Cross-platform child process management (subprocess + psutil).

Children are spawned detached so they survive WALoader restarts; identity is
pid + process create_time (PID reuse after a reboot must never match).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

import waloader
from waloader.config import WALoaderConfig
from waloader.logging_setup import app_log_dir
from waloader.models import App, AppVersion
from waloader.paths import ensure_dir
from waloader.repositories import runtime as runtime_repo
from waloader.services import layout, uv_env

log = logging.getLogger(__name__)

CREATE_TIME_TOLERANCE = 1.0  # seconds; filesystem/psutil rounding slack


class ProcessError(Exception):
    pass


def sdk_src_path() -> Path:
    """The src/ directory containing waloader_sdk, prepended to child PYTHONPATH."""
    return Path(waloader.__file__).resolve().parent.parent


def pid_matches(pid: int | None, create_time: float | None) -> bool:
    """True iff this exact process (pid AND creation time) is alive."""
    if pid is None or create_time is None:
        return False
    try:
        proc = psutil.Process(pid)
        return abs(proc.create_time() - create_time) < CREATE_TIME_TOLERANCE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def spawn_detached(
    command: list[str], *, cwd: Path, env: dict[str, str], log_file: Path
) -> tuple[int, float]:
    """Start a detached child; stdout/stderr append to log_file.

    POSIX: new session. Windows: new process group + detached (per AGENTS.md).
    Returns (pid, create_time).
    """
    ensure_dir(log_file.parent)
    kwargs: dict = {}
    if os.name == "nt":  # pragma: no cover - exercised on Windows machines
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    with open(log_file, "ab") as sink:
        proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            command,
            cwd=str(cwd),
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    try:
        create_time = psutil.Process(proc.pid).create_time()
    except psutil.NoSuchProcess:  # died instantly; the log file has the reason
        create_time = 0.0
    log.info("spawned pid=%s: %s", proc.pid, " ".join(command))
    return proc.pid, create_time


def terminate_tree(pid: int, create_time: float | None, *, timeout: int) -> bool:
    """Gracefully terminate a process and its children, killing after timeout."""
    if not pid_matches(pid, create_time) and create_time is not None:
        return False
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False
    procs = [root, *root.children(recursive=True)]
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)
    return True


# --- child app specifics -------------------------------------------------


def child_bind_address(config: WALoaderConfig) -> str:
    if config.apps.bind_address != "auto":
        return config.apps.bind_address
    return "127.0.0.1" if config.caddy.enabled else "0.0.0.0"


def child_base_url_path(config: WALoaderConfig, app: App) -> str | None:
    return f"apps/{app.slug}" if config.caddy.enabled else None


def child_command(
    config: WALoaderConfig, app: App, version: AppVersion
) -> tuple[list[str], Path]:
    """(argv, cwd) to launch a child app from its per-version venv."""
    venv_path = layout.venv_dir(config, app.slug, version.version_number)
    python = uv_env.venv_python(venv_path)
    source = layout.resolve(config, version.source_path)
    entrypoint = version.manifest["entrypoint"]
    if app.port is None:
        raise ProcessError(f"App '{app.slug}' has no allocated port")
    command = [
        str(python), "-m", "streamlit", "run", entrypoint,
        "--server.port", str(app.port),
        "--server.address", child_bind_address(config),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    base_path = child_base_url_path(config, app)
    if base_path:
        command += ["--server.baseUrlPath", base_path]
    return command, source


def child_env(config: WALoaderConfig, app: App) -> dict[str, str]:
    """Injected contract (G01 §3.6): WALOADER_* plus PYTHONPATH prepend."""
    env = dict(os.environ)
    env["WALOADER_APP_SLUG"] = app.slug
    env["WALOADER_APP_NAME"] = app.name
    env["WALOADER_DB_PATH"] = str(config.database_path)
    env["WALOADER_DATA_DIR"] = str(config.data_dir)
    env["WALOADER_DATASETS_DIR"] = str(layout.datasets_dir(config, app.slug))
    sdk = str(sdk_src_path())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = sdk + os.pathsep + existing if existing else sdk
    return env


def start_app(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, version: AppVersion
) -> tuple[int, float]:
    """Launch the child app detached and record pid/create_time."""
    command, cwd = child_command(config, app, version)
    log_file = app_log_dir(config, app.slug, version.version_number) / "runtime.log"
    pid, create_time = spawn_detached(
        command, cwd=cwd, env=child_env(config, app), log_file=log_file
    )
    runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=create_time)
    conn.commit()
    return pid, create_time


def is_app_running(conn: sqlite3.Connection, app: App) -> bool:
    rt = runtime_repo.get(conn, app.id)
    return rt is not None and pid_matches(rt.pid, rt.pid_create_time)


def stop_app(conn: sqlite3.Connection, config: WALoaderConfig, app: App) -> bool:
    """Stop the app's process tree; returns True if something was stopped."""
    rt = runtime_repo.get(conn, app.id)
    stopped = False
    if rt is not None and rt.pid is not None:
        stopped = terminate_tree(
            rt.pid, rt.pid_create_time, timeout=config.apps.stop_timeout_seconds
        )
    runtime_repo.clear_process(conn, app.id)
    conn.commit()
    return stopped


@dataclass(frozen=True)
class ProcessStatus:
    running: bool
    pid: int | None
    started_at: str | None
    detail: str


def app_status(conn: sqlite3.Connection, app: App) -> ProcessStatus:
    rt = runtime_repo.get(conn, app.id)
    if rt is None or rt.pid is None:
        return ProcessStatus(False, None, None, "no recorded process")
    if pid_matches(rt.pid, rt.pid_create_time):
        return ProcessStatus(True, rt.pid, rt.started_at, "process alive")
    return ProcessStatus(False, rt.pid, rt.started_at, "recorded pid is gone (or reused)")


def tail_log(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def waloader_src_python() -> str:
    """Interpreter running WALoader itself (used by serve/tools)."""
    return sys.executable
