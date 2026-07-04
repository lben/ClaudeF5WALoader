from __future__ import annotations

import time
from pathlib import Path

import pytest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import users as users_repo
from waloader.services.background import BackgroundWorker


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[health]\ninterval_seconds = 1\ngrace_period_seconds = 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    yield config, conn
    conn.close()


class TestTick:
    def test_health_and_daily_maintenance(self, env) -> None:
        config, conn = env
        user = users_repo.create(conn, "u", "u@x.com", "h")
        app = apps_repo.create(conn, owner_id=user.id, name="Dead", slug="dead")
        apps_repo.set_state(conn, app.id, "running")
        runtime_repo.upsert_started(conn, app.id, pid=2_111_111, pid_create_time=1.0)
        conn.commit()

        worker = BackgroundWorker()
        worker.tick()

        # health: the dead 'running' app was failed
        assert apps_repo.get(conn, app.id).state == "failed"
        # maintenance: the daily job ran exactly once and produced a backup
        assert worker.maintenance_runs == 1
        assert list(config.backups_dir.glob("waloader-*.db"))

        worker.tick()
        assert worker.maintenance_runs == 1  # same day: no second run
        assert worker.ticks == 2

    def test_thread_start_stop(self, env) -> None:
        config, conn = env
        worker = BackgroundWorker()
        worker.start()
        worker.start()  # idempotent
        deadline = time.monotonic() + 10
        while worker.ticks == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        worker.stop()
        assert worker.ticks >= 1
        assert not worker._thread.is_alive()

    def test_tick_survives_errors(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        config, conn = env
        worker = BackgroundWorker()
        from waloader.services import background as bg

        calls = {"n": 0}

        def boom() -> None:
            calls["n"] += 1
            raise RuntimeError("boom")

        monkeypatch.setattr(worker, "tick", boom)
        monkeypatch.setattr(bg, "_fresh_config", lambda: config)
        worker.start()
        deadline = time.monotonic() + 10
        while calls["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        worker.stop()
        assert calls["n"] >= 2  # loop keeps going after failures
