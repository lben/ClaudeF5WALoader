from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

from waloader.config import WALoaderConfig
from waloader.models import App, User
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import versions as versions_repo
from waloader.services import health, lifecycle, processes, reconciliation


@pytest.fixture
def fast_config(tmp_path: Path) -> WALoaderConfig:
    return WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "ports": {"child_app_start": 47880, "child_app_end": 47899},
            "health": {"initial_check_timeout_seconds": 1},
        }
    )


def _healthy(config, app, process_alive):
    return health.ProbeResult(True, "")


def _sleeper_launcher(conn, config, app, version):
    pid, create_time = processes.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=Path.cwd(), env=dict(os.environ),
        log_file=config.logs_dir / "sleeper.log",
    )
    runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=create_time)
    conn.commit()
    return pid, create_time


@pytest.fixture(autouse=True)
def _cleanup(conn: sqlite3.Connection, fast_config: WALoaderConfig):
    yield
    for app in apps_repo.list_all(conn, include_deleted=True):
        processes.stop_app(conn, fast_config, app)


def _deployed_app(conn: sqlite3.Connection, config, user: User, name: str, slug: str,
                  state: str = "stopped") -> App:
    from waloader.services import layout, uv_env

    app = apps_repo.create(conn, owner_id=user.id, name=name, slug=slug)
    versions_repo.create(
        conn, app_id=app.id, version_number=1, manifest={"entrypoint": "app.py"},
        bundle_path=f"apps/{slug}/versions/000001/uploaded_bundle.md",
        source_path=f"apps/{slug}/versions/000001/source",
        created_by=user.id,
    )
    apps_repo.set_current_version(conn, app.id, 1)
    apps_repo.set_state(conn, app.id, state)
    conn.commit()
    # a venv must exist for lifecycle.start (G02: missing venv => rebuild required)
    venv_python = uv_env.venv_python(layout.venv_dir(config, slug, 1))
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("")
    return apps_repo.get(conn, app.id)


class TestLifecycle:
    def test_start_stopped_app(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "A", "a")
        result = lifecycle.start(
            conn, fast_config, app, actor="alice",
            _launcher=_sleeper_launcher, _prober=_healthy,
        )
        assert result.ok, result.message
        app = apps_repo.get(conn, app.id)
        assert app.state == "running" and app.port is not None
        assert processes.is_app_running(conn, app)

    def test_start_already_running_is_noop(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "A", "a")
        lifecycle.start(conn, fast_config, app, _launcher=_sleeper_launcher,
                        _prober=_healthy)
        app = apps_repo.get(conn, app.id)
        result = lifecycle.start(conn, fast_config, app, _launcher=_sleeper_launcher,
                                 _prober=_healthy)
        assert result.ok and "already running" in result.message

    def test_start_without_version_fails(self, conn, fast_config, user: User) -> None:
        app = apps_repo.create(conn, owner_id=user.id, name="NoVer", slug="no-ver")
        conn.commit()
        result = lifecycle.start(conn, fast_config, app)
        assert not result.ok and "no deployed version" in result.message

    def test_start_failure_cleans_up(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "A", "a")

        def never_healthy(config, a, process_alive):
            return health.ProbeResult(False, "port closed")

        result = lifecycle.start(conn, fast_config, app,
                                 _launcher=_sleeper_launcher, _prober=never_healthy)
        assert not result.ok and "failed to start" in result.message
        assert not processes.is_app_running(conn, apps_repo.get(conn, app.id))
        assert apps_repo.get(conn, app.id).state == "stopped"  # unchanged

    def test_stop_running_app(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "A", "a")
        lifecycle.start(conn, fast_config, app, _launcher=_sleeper_launcher,
                        _prober=_healthy)
        app = apps_repo.get(conn, app.id)
        result = lifecycle.stop(conn, fast_config, app, actor="alice")
        assert result.ok
        app = apps_repo.get(conn, app.id)
        assert app.state == "stopped" and not processes.is_app_running(conn, app)

    def test_stop_and_restart_never_send_crash_email(
        self, conn, fast_config, user: User, monkeypatch
    ) -> None:
        """G01 §4.14: user-triggered stop/restart are never crash-notified."""
        from waloader.notifications import mailer

        sent: list = []
        monkeypatch.setattr(
            mailer, "send_mail",
            lambda **kwargs: sent.append(kwargs),
        )
        app = _deployed_app(conn, fast_config, user, "Quiet", "quiet")
        lifecycle.start(conn, fast_config, app, _launcher=_sleeper_launcher,
                        _prober=_healthy)
        app = apps_repo.get(conn, app.id)
        lifecycle.stop(conn, fast_config, app, actor="alice")
        app = apps_repo.get(conn, app.id)
        lifecycle.restart(conn, fast_config, app, _launcher=_sleeper_launcher,
                          _prober=_healthy)
        assert sent == []

    def test_restart_changes_pid(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "A", "a")
        lifecycle.start(conn, fast_config, app, _launcher=_sleeper_launcher,
                        _prober=_healthy)
        first_pid = runtime_repo.get(conn, app.id).pid
        app = apps_repo.get(conn, app.id)
        result = lifecycle.restart(conn, fast_config, app,
                                   _launcher=_sleeper_launcher, _prober=_healthy)
        assert result.ok and "restarted" in result.message
        assert runtime_repo.get(conn, app.id).pid != first_pid


class TestReconciliation:
    def test_dead_running_app_becomes_stopped_resume_candidate(
        self, conn, fast_config, user: User
    ) -> None:
        app = _deployed_app(conn, fast_config, user, "Dead", "dead", state="running")
        runtime_repo.upsert_started(conn, app.id, pid=2_111_111, pid_create_time=1.0)
        conn.commit()
        report = reconciliation.reconcile(conn, fast_config)
        assert report.checked == 1
        assert report.resume_candidates == ["dead"]
        assert report.actions[0].action == "marked stopped"
        assert apps_repo.get(conn, app.id).state == "stopped"

    def test_alive_running_app_untouched(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "Live", "live")
        lifecycle.start(conn, fast_config, app, _launcher=_sleeper_launcher,
                        _prober=_healthy)
        report = reconciliation.reconcile(conn, fast_config)
        assert report.actions == [] and report.resume_candidates == []
        assert apps_repo.get(conn, app.id).state == "running"

    def test_stopped_app_with_alive_process_adopted(
        self, conn, fast_config, user: User
    ) -> None:
        app = _deployed_app(conn, fast_config, user, "Zombie", "zombie")
        _sleeper_launcher(conn, fast_config, app, None)  # process alive, state stopped
        report = reconciliation.reconcile(conn, fast_config)
        assert report.actions[0].action == "adopted running process"
        assert apps_repo.get(conn, app.id).state == "running"

    def test_resume_apps(self, conn, fast_config, user: User) -> None:
        app = _deployed_app(conn, fast_config, user, "Res", "res", state="running")
        runtime_repo.upsert_started(conn, app.id, pid=2_111_111, pid_create_time=1.0)
        conn.commit()
        report = reconciliation.reconcile(conn, fast_config)
        results = reconciliation.resume_apps(
            conn, fast_config, report.resume_candidates, actor="admin",
            _launcher=_sleeper_launcher, _prober=_healthy,
        )
        assert all(r.ok for _, r in results)
        assert apps_repo.get(conn, app.id).state == "running"

    def test_resume_unknown_slug(self, conn, fast_config) -> None:
        results = reconciliation.resume_apps(conn, fast_config, ["ghost"],
                                             _prober=_healthy)
        assert not results[0][1].ok

    def test_overview(self, conn, fast_config, user: User) -> None:
        _deployed_app(conn, fast_config, user, "One", "one")
        rows = reconciliation.apps_overview(conn)
        assert rows[0]["slug"] == "one" and rows[0]["state"] == "stopped"
        assert rows[0]["process"] == "-"
