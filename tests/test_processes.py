from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import versions as versions_repo
from waloader.services import processes

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def sleeper(tmp_path: Path) -> Iterator[tuple[int, float]]:
    pid, create_time = processes.spawn_detached(
        SLEEPER, cwd=tmp_path, env=dict(os.environ), log_file=tmp_path / "sleep.log"
    )
    yield pid, create_time
    processes.terminate_tree(pid, create_time, timeout=5)


class TestSpawnAndIdentity:
    def test_spawn_detached_and_pid_matches(self, sleeper: tuple[int, float]) -> None:
        pid, create_time = sleeper
        assert processes.pid_matches(pid, create_time)

    def test_wrong_create_time_never_matches(self, sleeper: tuple[int, float]) -> None:
        pid, create_time = sleeper
        assert not processes.pid_matches(pid, create_time + 9999.0)  # PID-reuse guard

    def test_none_never_matches(self) -> None:
        assert not processes.pid_matches(None, None)
        assert not processes.pid_matches(12345, None)

    def test_terminate_tree(self, tmp_path: Path) -> None:
        pid, create_time = processes.spawn_detached(
            SLEEPER, cwd=tmp_path, env=dict(os.environ), log_file=tmp_path / "s.log"
        )
        assert processes.terminate_tree(pid, create_time, timeout=5)
        assert not processes.pid_matches(pid, create_time)

    def test_terminate_gone_process_is_false(self) -> None:
        assert not processes.terminate_tree(2_111_111, 1.0, timeout=1)

    def test_child_writes_to_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "out.log"
        pid, create_time = processes.spawn_detached(
            [sys.executable, "-c", "print('hello from child')"],
            cwd=tmp_path, env=dict(os.environ), log_file=log_file,
        )
        psutil.wait_procs([psutil.Process(pid)] if psutil.pid_exists(pid) else [], timeout=10)
        assert "hello from child" in log_file.read_text()


class TestChildEnv:
    def test_contract(self, config: WALoaderConfig, app: App,
                      monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYTHONPATH", "/existing/path")
        env = processes.child_env(config, app)
        assert env["WALOADER_APP_SLUG"] == app.slug
        assert env["WALOADER_APP_NAME"] == app.name
        assert env["WALOADER_DB_PATH"] == str(config.database_path)
        assert env["WALOADER_DATA_DIR"] == str(config.data_dir)
        assert env["WALOADER_DATASETS_DIR"].endswith(
            os.path.join("apps", app.slug, "datasets")
        )
        sdk, rest = env["PYTHONPATH"].split(os.pathsep, 1)
        assert (Path(sdk) / "waloader_sdk").is_dir()  # points at real src/
        assert rest == "/existing/path"  # existing PYTHONPATH preserved

    def test_no_existing_pythonpath(self, config: WALoaderConfig, app: App,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
        env = processes.child_env(config, app)
        assert os.pathsep not in env["PYTHONPATH"]


class TestChildCommand:
    def _version(self, conn: sqlite3.Connection, config: WALoaderConfig, app: App):
        source = config.apps_dir / app.slug / "versions" / "000001" / "source"
        source.mkdir(parents=True)
        return versions_repo.create(
            conn, app_id=app.id, version_number=1,
            manifest={"entrypoint": "app.py"},
            bundle_path=f"apps/{app.slug}/versions/000001/uploaded_bundle.md",
            source_path=f"apps/{app.slug}/versions/000001/source",
            created_by=None,
        )

    def test_direct_mode_flags(self, conn: sqlite3.Connection, config: WALoaderConfig,
                               app: App) -> None:
        version = self._version(conn, config, app)
        from waloader.repositories import apps as apps_repo

        apps_repo.set_port(conn, app.id, 8888)
        app = apps_repo.get(conn, app.id)
        command, cwd = processes.child_command(config, app, version)
        joined = " ".join(command)
        assert "-m streamlit run app.py" in joined
        assert "--server.port 8888" in joined
        assert "--server.address 0.0.0.0" in joined  # direct mode binds all interfaces
        assert "--server.headless true" in joined
        assert "--baseUrlPath" not in joined
        assert cwd.name == "source"

    def test_caddy_mode_flags(self, conn: sqlite3.Connection, tmp_path: Path,
                              app: App) -> None:
        config = WALoaderConfig.model_validate(
            {"paths": {"data_dir": str(tmp_path / "data")}, "caddy": {"enabled": True}}
        )
        version = self._version(conn, config, app)
        from waloader.repositories import apps as apps_repo

        apps_repo.set_port(conn, app.id, 8888)
        app = apps_repo.get(conn, app.id)
        command, _ = processes.child_command(config, app, version)
        joined = " ".join(command)
        assert f"--server.baseUrlPath apps/{app.slug}" in joined
        assert "--server.address 127.0.0.1" in joined  # only the proxy reaches it

    def test_no_port_raises(self, conn: sqlite3.Connection, config: WALoaderConfig,
                            app: App) -> None:
        version = self._version(conn, config, app)
        with pytest.raises(processes.ProcessError, match="no allocated port"):
            processes.child_command(config, app, version)


class TestAppLifecycleRecords:
    def test_status_stop_roundtrip(self, conn: sqlite3.Connection,
                                   config: WALoaderConfig, app: App,
                                   sleeper: tuple[int, float]) -> None:
        pid, create_time = sleeper
        runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=create_time)
        conn.commit()

        assert processes.is_app_running(conn, app)
        status = processes.app_status(conn, app)
        assert status.running and status.pid == pid

        assert processes.stop_app(conn, config, app)
        assert not processes.is_app_running(conn, app)
        assert runtime_repo.get(conn, app.id).pid is None

    def test_status_with_no_record(self, conn: sqlite3.Connection, app: App) -> None:
        status = processes.app_status(conn, app)
        assert not status.running and "no recorded process" in status.detail

    def test_stale_pid_reported(self, conn: sqlite3.Connection, app: App) -> None:
        runtime_repo.upsert_started(conn, app.id, pid=2_111_111, pid_create_time=1.0)
        conn.commit()
        status = processes.app_status(conn, app)
        assert not status.running and "gone" in status.detail


class TestTailLog:
    def test_tail(self, tmp_path: Path) -> None:
        log_file = tmp_path / "x.log"
        log_file.write_text("\n".join(f"line{i}" for i in range(500)))
        tail = processes.tail_log(log_file, lines=3)
        assert tail == "line497\nline498\nline499"
        assert processes.tail_log(tmp_path / "missing.log") == ""


class TestJsonSafety:
    def test_spawn_records_are_json_safe(self, sleeper: tuple[int, float]) -> None:
        json.dumps({"pid": sleeper[0], "create_time": sleeper[1]})
