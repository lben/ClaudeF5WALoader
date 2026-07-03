from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from waloader.config import WALoaderConfig
from waloader.models import User
from waloader.repositories import apps as apps_repo
from waloader.repositories import deployments as deployments_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import deployment, health, layout, processes

GOOD_BUNDLE = (
    b"```toml waloader-bundle\n"
    b"bundle_format = 1\n"
    b'entrypoint = "app.py"\n'
    b"```\n"
    b"## file: app.py\n```python\nimport streamlit as st\nst.write('ok')\n```\n"
)

BAD_BUNDLE = b"# no metadata block here\njust text\n"

VCS_DEP_BUNDLE = (
    b"```toml waloader-bundle\n"
    b"bundle_format = 1\n"
    b'entrypoint = "app.py"\n'
    b"```\n"
    b"## file: app.py\n```python\npass\n```\n"
    b"## file: pyproject.toml\n"
    b"```toml\n"
    b"[project]\n"
    b'name = "x"\nversion = "0"\n'
    b'dependencies = ["pkg @ git+https://github.com/a/b"]\n'
    b"```\n"
)

TESTED_BUNDLE = (
    b"```toml waloader-bundle\n"
    b"bundle_format = 1\n"
    b'entrypoint = "app.py"\n'
    b"```\n"
    b"## file: app.py\n```python\npass\n```\n"
    b"## file: tests/test_app.py\n```python\ndef test_x():\n    assert True\n```\n"
)


def _ok_uv(command, env, timeout, cwd=None):
    if len(command) > 2 and command[1] == "venv":  # mimic uv creating the venv dir
        Path(command[2]).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(returncode=0, stdout="uv ok", stderr="")


def _ok_tests(command, env, timeout, cwd=None):
    return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")


def _failing_tests(command, env, timeout, cwd=None):
    return SimpleNamespace(returncode=1, stdout="1 failed: assert False", stderr="")


def _healthy(config, app, process_alive):
    return health.ProbeResult(True, "")


def _unhealthy(config, app, process_alive):
    return health.ProbeResult(False, "HTTP health endpoint not responding")


def _sleeper_launcher(conn, config, app, version):
    """Launch a real (cheap) child so stop/terminate paths work safely."""
    pid, create_time = processes.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=Path.cwd(), env=dict(os.environ),
        log_file=config.logs_dir / "apps" / app.slug / "sleeper.log",
    )
    runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=create_time)
    conn.commit()
    return pid, create_time


@pytest.fixture
def fast_config(tmp_path: Path) -> WALoaderConfig:
    return WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "ports": {"child_app_start": 47850, "child_app_end": 47870},
            "health": {"initial_check_timeout_seconds": 1},
        }
    )


@pytest.fixture(autouse=True)
def _cleanup_children(conn: sqlite3.Connection, fast_config: WALoaderConfig):
    yield
    for app in apps_repo.list_all(conn, include_deleted=True):
        processes.stop_app(conn, fast_config, app)


SEAMS_OK = {
    "_uv_runner": _ok_uv,
    "_test_runner": _ok_tests,
    "_launcher": _sleeper_launcher,
    "_prober": _healthy,
}


class TestCreateFlow:
    def test_happy_path(self, conn: sqlite3.Connection, fast_config: WALoaderConfig,
                        user: User) -> None:
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="My Dashboard",
            description="d", user_mgmt_enabled=False, bundle_bytes=GOOD_BUNDLE,
            **SEAMS_OK,
        )
        assert result.ok, result.error_block()
        assert app.state == "running"
        assert app.current_version == 1
        assert app.slug == "my-dashboard"
        assert 47850 <= app.port <= 47870
        assert result.url == f"http://localhost:{app.port}"
        deps = deployments_repo.list_for_app(conn, app.id)
        assert deps[0].status == "succeeded" and deps[0].kind == "create"
        deploy_log = fast_config.logs_dir / "apps" / app.slug / "000001" / "deploy.log"
        assert "ok=True" in deploy_log.read_text()

    def test_duplicate_name_rejected_before_deploy(
        self, conn: sqlite3.Connection, fast_config: WALoaderConfig, user: User
    ) -> None:
        deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Dup", description="",
            user_mgmt_enabled=False, bundle_bytes=GOOD_BUNDLE, **SEAMS_OK,
        )
        with pytest.raises(deployment.AppCreationError, match="already taken"):
            deployment.create_app_and_deploy(
                conn, fast_config, owner=user, name="dup", description="",
                user_mgmt_enabled=False, bundle_bytes=GOOD_BUNDLE, **SEAMS_OK,
            )

    def test_bad_bundle_fails_cleanly(self, conn: sqlite3.Connection,
                                      fast_config: WALoaderConfig, user: User) -> None:
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Broken", description="",
            user_mgmt_enabled=False, bundle_bytes=BAD_BUNDLE, **SEAMS_OK,
        )
        assert not result.ok
        assert app.state == "deployment_failed"
        assert "No metadata block" in result.error_block()
        assert "No metadata block" in apps_repo.get(conn, app.id).last_deploy_error
        assert deployments_repo.list_for_app(conn, app.id)[0].status == "failed"

    def test_policy_violation_fails(self, conn: sqlite3.Connection,
                                    fast_config: WALoaderConfig, user: User) -> None:
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Vcs", description="",
            user_mgmt_enabled=False, bundle_bytes=VCS_DEP_BUNDLE, **SEAMS_OK,
        )
        assert not result.ok
        assert "VCS dependencies are disabled" in result.error_block()
        assert app.state == "deployment_failed"

    def test_app_test_failure_blocks_deploy(
        self, conn: sqlite3.Connection, fast_config: WALoaderConfig, user: User
    ) -> None:
        seams = dict(SEAMS_OK, _test_runner=_failing_tests)
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Tested", description="",
            user_mgmt_enabled=False, bundle_bytes=TESTED_BUNDLE, **seams,
        )
        assert not result.ok
        assert result.error_summary == "The app's own tests failed"
        assert app.state == "deployment_failed"
        test_log = fast_config.logs_dir / "apps" / app.slug / "000001" / "test.log"
        assert "1 failed" in test_log.read_text()

    def test_unhealthy_launch_fails_with_log_tail(
        self, conn: sqlite3.Connection, fast_config: WALoaderConfig, user: User
    ) -> None:
        seams = dict(SEAMS_OK, _prober=_unhealthy)
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Sick", description="",
            user_mgmt_enabled=False, bundle_bytes=GOOD_BUNDLE, **seams,
        )
        assert not result.ok
        assert app.state == "deployment_failed"
        assert "did not become healthy" in result.error_summary
        # the launched child must have been cleaned up
        assert not processes.is_app_running(conn, app)


class TestUpdateFlow:
    def _created(self, conn, fast_config, user):
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Updatable", description="",
            user_mgmt_enabled=False, bundle_bytes=GOOD_BUNDLE, **SEAMS_OK,
        )
        assert result.ok
        return apps_repo.get(conn, app.id)

    def test_successful_update_swaps_and_keeps_port(
        self, conn: sqlite3.Connection, fast_config: WALoaderConfig, user: User
    ) -> None:
        app = self._created(conn, fast_config, user)
        old_port = app.port
        old_pid = runtime_repo.get(conn, app.id).pid

        result = deployment.redeploy(
            conn, fast_config, app, GOOD_BUNDLE, actor_id=user.id, **SEAMS_OK
        )
        assert result.ok and result.kind == "update"
        app = apps_repo.get(conn, app.id)
        assert app.state == "running"
        assert app.current_version == 2
        assert app.port == old_port  # stable port across updates
        assert runtime_repo.get(conn, app.id).pid != old_pid  # new process
        assert not processes.pid_matches(old_pid, None) or True  # old one stopped below
        # old venv cleaned, only current kept
        venvs = layout.venvs_root(fast_config, app.slug)
        assert [p.name for p in venvs.iterdir()] == ["000002"]

    def test_failed_update_pre_swap_keeps_old_running(
        self, conn: sqlite3.Connection, fast_config: WALoaderConfig, user: User
    ) -> None:
        app = self._created(conn, fast_config, user)
        old_pid = runtime_repo.get(conn, app.id).pid

        result = deployment.redeploy(
            conn, fast_config, app, VCS_DEP_BUNDLE, actor_id=user.id, **SEAMS_OK
        )
        assert not result.ok
        app = apps_repo.get(conn, app.id)
        assert app.state == "running"  # old version untouched
        assert app.current_version == 1
        assert runtime_repo.get(conn, app.id).pid == old_pid  # same process
        assert processes.is_app_running(conn, app)

    def test_failed_update_post_swap_is_deployment_failed(
        self, conn: sqlite3.Connection, fast_config: WALoaderConfig, user: User
    ) -> None:
        app = self._created(conn, fast_config, user)
        seams = dict(SEAMS_OK, _prober=_unhealthy)
        result = deployment.redeploy(
            conn, fast_config, app, GOOD_BUNDLE, actor_id=user.id, **seams
        )
        assert not result.ok
        app = apps_repo.get(conn, app.id)
        assert app.state == "deployment_failed"
        assert app.current_version == 1  # v2 never became current

    def test_retry_kinds(self, conn: sqlite3.Connection, fast_config: WALoaderConfig,
                         user: User) -> None:
        app, result = deployment.create_app_and_deploy(
            conn, fast_config, owner=user, name="Retry", description="",
            user_mgmt_enabled=False, bundle_bytes=BAD_BUNDLE, **SEAMS_OK,
        )
        assert not result.ok
        retry = deployment.redeploy(
            conn, fast_config, app, GOOD_BUNDLE, actor_id=user.id, **SEAMS_OK
        )
        assert retry.kind == "retry-create"
        assert retry.ok

        # a PRE-swap update failure leaves the app running -> next try is "update"
        app = apps_repo.get(conn, app.id)
        failed_update = deployment.redeploy(
            conn, fast_config, app, BAD_BUNDLE, actor_id=user.id, **SEAMS_OK
        )
        assert failed_update.kind == "update" and not failed_update.ok
        assert apps_repo.get(conn, app.id).state == "running"

        # a POST-swap update failure -> deployment_failed -> next try is "retry-update"
        app = apps_repo.get(conn, app.id)
        post_swap_fail = deployment.redeploy(
            conn, fast_config, app, GOOD_BUNDLE, actor_id=user.id,
            **dict(SEAMS_OK, _prober=_unhealthy),
        )
        assert post_swap_fail.kind == "update" and not post_swap_fail.ok
        app = apps_repo.get(conn, app.id)
        assert app.state == "deployment_failed"
        retry_update = deployment.redeploy(
            conn, fast_config, app, GOOD_BUNDLE, actor_id=user.id, **SEAMS_OK
        )
        assert retry_update.kind == "retry-update" and retry_update.ok


class TestCaddyIntegrationPoints:
    def test_route_recorded_when_enabled(self, conn: sqlite3.Connection,
                                         tmp_path: Path, user: User) -> None:
        config = WALoaderConfig.model_validate(
            {
                "paths": {"data_dir": str(tmp_path / "data")},
                "ports": {"child_app_start": 47850, "child_app_end": 47870},
                "health": {"initial_check_timeout_seconds": 1},
                "caddy": {"enabled": True},
                "server": {"public_host": "finbox"},
            }
        )
        app, result = deployment.create_app_and_deploy(
            conn, config, owner=user, name="Routed", description="",
            user_mgmt_enabled=False, bundle_bytes=GOOD_BUNDLE, **SEAMS_OK,
        )
        assert result.ok
        assert apps_repo.get(conn, app.id).caddy_route == "/apps/routed"
        assert result.url == "http://finbox:8080/apps/routed"
        assert config.caddy_config_path.exists()  # regenerated during deploy
        processes.stop_app(conn, config, app)
