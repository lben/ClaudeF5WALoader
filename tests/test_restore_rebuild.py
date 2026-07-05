from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from waloader import db as wdb
from waloader.config import WALoaderConfig
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.services import (
    app_users_service,
    datasets_service,
    deployment,
    layout,
    lifecycle,
    restore,
    uv_env,
    versioning,
)
from waloader.services import bundles as bundles_service
from waloader.services import scoped_backups as sb

BUNDLE_TEXT = (
    "```toml waloader-bundle\n"
    "bundle_format = 1\n"
    'entrypoint = "app.py"\n'
    "```\n"
    "## file: app.py\n```python\nimport streamlit as st\nst.write('v1')\n```\n"
)


@pytest.fixture
def env(tmp_path: Path):
    """Config-located DB, one app with a REAL preserved bundle + dataset."""
    config = WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "ports": {"child_app_start": 47920, "child_app_end": 47940},
            "health": {"initial_check_timeout_seconds": 1},
        }
    )
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "owner", "o@x.com", "hash", is_admin=True)
    app = apps_repo.create(conn, owner_id=owner.id, name="Demo App", slug="demo-app")
    parsed = bundles_service.parse_bundle(BUNDLE_TEXT)
    versioning.create_version(conn, config, app, parsed, BUNDLE_TEXT.encode(),
                              created_by=owner.id)
    apps_repo.set_current_version(conn, app.id, 1)
    apps_repo.set_state(conn, app.id, "running")  # pretend it was live
    concept = datasets_service.create_concept(conn, app, "clients")
    datasets_service.store_upload(
        conn, config, app, concept, filename="c.csv",
        data=pd.DataFrame({"id": [1, 2]}).to_csv(index=False).encode(),
    )
    app_users_service.create_app_user(conn, app, username="jdoe", email="",
                                      password="proper-pw-1")
    conn.commit()
    yield config, conn, owner, apps_repo.get(conn, app.id)
    conn.close()


def _ok_uv(command, env, timeout, cwd=None):
    if len(command) > 2 and command[1] == "venv":
        Path(command[2]).mkdir(parents=True, exist_ok=True)
        uv_env.venv_python(Path(command[2])).parent.mkdir(parents=True, exist_ok=True)
        uv_env.venv_python(Path(command[2])).write_text("")
    return SimpleNamespace(returncode=0, stdout="uv ok", stderr="")


def _sleeper_launcher(conn, config, app, version):
    import os
    import sys

    from waloader.repositories import runtime as runtime_repo
    from waloader.services import processes

    pid, create_time = processes.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=Path.cwd(), env=dict(os.environ),
        log_file=config.logs_dir / "sleeper.log",
    )
    runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=create_time)
    conn.commit()
    return pid, create_time


def _healthy(config, app, process_alive):
    from waloader.services import health

    return health.ProbeResult(True, "")


SEAMS = {"_uv_runner": _ok_uv, "_launcher": _sleeper_launcher, "_prober": _healthy}


@pytest.fixture(autouse=True)
def _cleanup(request):
    yield
    # stop any sleepers the seams launched (best effort; envs are per-test)


class TestWipe:
    def test_wipe_preserves_backups_only(self, env) -> None:
        config, conn, owner, app = env
        conn.close()
        sb.manual_dir(config).mkdir(parents=True, exist_ok=True)
        (sb.manual_dir(config) / "keep.zip").write_text("keep me")

        report = restore.wipe_data_dir(config)
        assert not config.database_path.exists()
        assert not (config.data_dir / "apps").exists()
        assert (sb.manual_dir(config) / "keep.zip").read_text() == "keep me"
        assert "apps" in report.removed and "waloader.db" in report.removed
        assert report.leftovers == []
        # reopen for fixture teardown
        env_conn = wdb.connect(config.database_path)
        env_conn.close()


class TestRestoreValidation:
    def test_refuses_over_existing_db_without_force(self, env, tmp_path) -> None:
        config, conn, owner, app = env
        backup = sb.create_backup(conn, config, "all")
        with pytest.raises(restore.RestoreError, match="--force"):
            restore.restore_all(config, backup.path)

    def test_rejects_non_all_scope(self, env) -> None:
        config, conn, owner, app = env
        backup = sb.create_backup(conn, config, "db")
        with pytest.raises(restore.RestoreError, match="all-scope"):
            restore.restore_all(config, backup.path, force=True)

    def test_rejects_foreign_zip(self, env, tmp_path: Path) -> None:
        config, conn, owner, app = env
        junk = tmp_path / "junk.zip"
        junk.write_text("not a zip")
        with pytest.raises(restore.RestoreError, match="Not a readable"):
            restore.restore_all(config, junk)

    def test_rejects_zip_slip_member(self, env, tmp_path: Path) -> None:
        config, conn, owner, app = env
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("manifest.json", json.dumps(
                {"archive_format": 2, "kind": "backup", "scope": "all"}
            ))
            archive.writestr("../evil.txt", "escape")
        with pytest.raises(restore.RestoreError, match="escapes the data directory"):
            restore.restore_all(config, evil, force=True)


class TestRestoreRoundTrip:
    def test_backup_wipe_restore_fidelity(self, env) -> None:
        config, conn, owner, app = env
        backup = sb.create_backup(conn, config, "all")
        conn.close()

        restore.wipe_data_dir(config)
        assert not config.database_path.exists()

        report = restore.restore_all(config, backup.path)
        assert report.files_restored > 0
        assert report.apps == 1
        assert report.rebuild_required == ["demo-app"]
        assert any("rebuild" in n for n in report.notes)

        fresh = wdb.connect(config.database_path)
        try:
            restored = apps_repo.get_by_slug(fresh, "demo-app")
            assert restored is not None
            assert restored.state == "stopped"  # was running: normalized
            assert restored.current_version == 1
            assert fresh.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
            assert fresh.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 1
            assert fresh.execute(
                "SELECT COUNT(*) FROM dataset_concepts"
            ).fetchone()[0] == 1
            source = layout.source_dir(config, "demo-app", 1)
            assert (source / "app.py").exists()
            parquet = layout.concept_dir(config, "demo-app", "clients") / "current.parquet"
            assert parquet.exists()
            bundle = layout.bundle_path(config, "demo-app", 1)
            assert bundle.read_bytes() == BUNDLE_TEXT.encode()
        finally:
            fresh.close()

    def test_restore_force_replaces_existing(self, env) -> None:
        config, conn, owner, app = env
        backup = sb.create_backup(conn, config, "all")
        # mutate current state after the backup
        apps_repo.set_description(conn, app.id, "MUTATED AFTER BACKUP")
        conn.commit()
        conn.close()

        report = restore.restore_all(config, backup.path, force=True)
        assert report.wipe is not None and "waloader.db" in report.wipe.removed
        fresh = wdb.connect(config.database_path)
        try:
            assert apps_repo.get_by_slug(fresh, "demo-app").description == "d" or \
                apps_repo.get_by_slug(fresh, "demo-app").description == ""
        finally:
            fresh.close()


class TestRebuild:
    def test_rebuild_replays_pipeline_on_preserved_bundle(self, env) -> None:
        config, conn, owner, app = env
        apps_repo.set_state(conn, app.id, "stopped")
        conn.commit()
        assert deployment.needs_rebuild(config, app)

        result = deployment.rebuild_app(conn, config, app, actor_id=owner.id, **SEAMS)
        assert result.ok, result.error_block()
        assert result.kind == "rebuild"
        app = apps_repo.get(conn, app.id)
        assert app.state == "running"
        assert app.current_version == 2  # rebuild appends a version, honestly
        assert not deployment.needs_rebuild(config, app)
        from waloader.services import processes

        processes.stop_app(conn, config, app)

    def test_rebuild_without_version(self, env) -> None:
        config, conn, owner, app = env
        bare = apps_repo.create(conn, owner_id=owner.id, name="Bare", slug="bare")
        conn.commit()
        result = deployment.rebuild_app(conn, config, bare)
        assert not result.ok and "no deployed version" in result.error_summary

    def test_rebuild_with_missing_bundle(self, env) -> None:
        config, conn, owner, app = env
        layout.bundle_path(config, app.slug, 1).unlink()
        result = deployment.rebuild_app(conn, config, app)
        assert not result.ok and "missing" in result.error_summary


class TestStartRequiresVenv:
    def test_start_names_rebuild_when_venv_missing(self, env) -> None:
        config, conn, owner, app = env
        apps_repo.set_state(conn, app.id, "stopped")
        conn.commit()
        result = lifecycle.start(conn, config, app)
        assert not result.ok
        assert "rebuild required" in result.message
        assert "appctl rebuild demo-app" in result.message


class TestApctlRebuildCli:
    def test_rebuild_requires_slug_or_all(self, env, monkeypatch, tmp_path) -> None:
        from waloader.tools import appctl

        toml = tmp_path / "w.toml"
        toml.write_text(f'[paths]\ndata_dir = "{env[0].data_dir}"\n', encoding="utf-8")
        monkeypatch.setenv("WALOADER_CONFIG", str(toml))
        with pytest.raises(SystemExit):
            appctl.main(["rebuild"])
        with pytest.raises(SystemExit):
            appctl.main(["rebuild", "demo-app", "--all"])
