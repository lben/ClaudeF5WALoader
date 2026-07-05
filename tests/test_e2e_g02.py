"""G02 end-to-end: real restore→rebuild and import round trips.

    uv run pytest -m e2e
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.services import app_migration, deployment, lifecycle, processes, restore
from waloader.services import scoped_backups as sb

pytestmark = pytest.mark.e2e

BUNDLE = Path("examples/sample-bundle.md")


def _uv_cache_dir() -> str | None:
    uv = shutil.which("uv")
    if uv is None:
        return None
    result = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True,
                            timeout=30)
    return result.stdout.strip() or None


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status == 200
    except Exception:
        return False


def _wait_healthy(port: int, seconds: int = 30) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _http_ok(f"http://127.0.0.1:{port}/_stcore/health"):
            return True
        time.sleep(0.5)
    return False


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    toml = tmp_path / "waloader.toml"
    cache = _uv_cache_dir()
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[ports]\nchild_app_start = 48661\nchild_app_end = 48680\n"
        "[health]\ninitial_check_timeout_seconds = 90\n"
        + (f'[uv]\ncache_dir = "{cache}"\n' if cache else ""),
        encoding="utf-8",
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "e2e", "e2e@x.com", "hash", is_admin=True)
    conn.commit()
    yield config, conn, owner
    # tests may close conn and/or replace the DB via restore — clean up fresh
    try:
        conn.close()
    except Exception:
        pass
    if config.database_path.exists():
        fresh = wdb.connect(config.database_path)
        for app in apps_repo.list_all(fresh, include_deleted=True):
            processes.stop_app(fresh, config, app)
        fresh.close()


class TestRestoreRebuildRoundTrip:
    def test_backup_wipe_restore_rebuild_serves(self, env) -> None:
        config, conn, owner = env

        app, result = deployment.create_app_and_deploy(
            conn, config, owner=owner, name="Client Positions", description="",
            user_mgmt_enabled=False, bundle_bytes=BUNDLE.read_bytes(),
        )
        assert result.ok, result.error_block()
        assert _wait_healthy(app.port)

        lifecycle.stop(conn, config, app)  # stop serve/children before backup+wipe
        backup = sb.create_backup(conn, config, "all", actor="e2e")
        conn.close()

        restore.wipe_data_dir(config)
        report = restore.restore_all(config, backup.path)
        assert report.rebuild_required == ["client-positions"]

        fresh = wdb.connect(config.database_path)
        try:
            restored = apps_repo.get_by_slug(fresh, "client-positions")
            assert restored is not None and restored.state == "stopped"

            # start refuses without a venv, naming the fix
            refused = lifecycle.start(fresh, config, restored)
            assert not refused.ok and "rebuild required" in refused.message

            rebuilt = deployment.rebuild_app(fresh, config, restored,
                                             actor_id=None)
            assert rebuilt.ok, rebuilt.error_block()
            restored = apps_repo.get_by_slug(fresh, "client-positions")
            assert restored.state == "running"
            assert _wait_healthy(restored.port)
            processes.stop_app(fresh, config, restored)
        finally:
            fresh.close()


class TestImportRoundTrip:
    def test_export_delete_import_serves(self, env) -> None:
        from waloader.services import deletion

        config, conn, owner = env
        app, result = deployment.create_app_and_deploy(
            conn, config, owner=owner, name="Client Positions", description="",
            user_mgmt_enabled=False, bundle_bytes=BUNDLE.read_bytes(),
        )
        assert result.ok, result.error_block()

        # export while alive, then soft-delete (name stays reserved)
        exported = app_migration.export_app(conn, config, app, actor="e2e")
        deletion.soft_delete_app(conn, config, apps_repo.get(conn, app.id))

        # import the export under a new name (old name reserved by the deleted row)
        imported, deploy_result = app_migration.import_app(
            conn, config, exported, new_name="Client Positions Two", actor="e2e",
        )
        assert deploy_result is not None and deploy_result.ok, (
            deploy_result.error_block() if deploy_result else "no deploy"
        )
        assert imported.slug == "client-positions-two"
        imported = apps_repo.get(conn, imported.id)
        assert imported.state == "running"
        assert _wait_healthy(imported.port)
        processes.stop_app(conn, config, imported)

    def test_undelete_from_soft_delete_archive(self, env) -> None:
        from waloader.services import deletion

        config, conn, owner = env
        app, result = deployment.create_app_and_deploy(
            conn, config, owner=owner, name="Undelete Me", description="",
            user_mgmt_enabled=False, bundle_bytes=BUNDLE.read_bytes(),
        )
        assert result.ok, result.error_block()
        archive = deletion.soft_delete_app(conn, config, apps_repo.get(conn, app.id))

        restored, deploy_result = app_migration.import_app(
            conn, config, archive, new_name="Undelete Me Again",
            deploy=False, actor="e2e",
        )
        assert deploy_result is None
        assert restored.state == "created"
        assert restored.current_version == 1
        assert deployment.needs_rebuild(config, restored)
