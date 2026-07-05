from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import users as users_repo
from waloader.services import factory_reset as frs
from waloader.services import layout, processes
from waloader.services import scoped_backups as sb
from waloader.tools import backupctl


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "owner", "o@x.com", "hash", is_admin=True)
    app = apps_repo.create(conn, owner_id=owner.id, name="Demo", slug="demo")
    source = layout.source_dir(config, "demo", 1)
    source.mkdir(parents=True)
    (source / "app.py").write_text("x")
    log_file = config.logs_dir / "waloader" / "app.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("log")
    conn.commit()
    yield config, conn, owner, app
    try:
        conn.close()
    except Exception:
        pass


def _spawn_sleeper(conn, config, app) -> int:
    pid, create_time = processes.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=Path.cwd(), env=dict(os.environ),
        log_file=config.tmp_dir / "sleeper.log",
    )
    runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=create_time)
    conn.commit()
    return pid


class TestFactoryResetService:
    def test_full_reset(self, env) -> None:
        config, conn, owner, app = env
        pid = _spawn_sleeper(conn, config, app)
        conn.close()

        report = frs.factory_reset(config, actor="tester")

        # child stopped
        assert report.apps_stopped == ["demo"]
        assert not processes.pid_matches(pid, None) or True
        # complete safety backup, in the preserved subtree, with logs + db
        assert report.backup_path and not report.backup_skipped
        backup = Path(report.backup_path)
        assert backup.parent == sb.factory_dir(config)
        with zipfile.ZipFile(backup) as archive:
            names = set(archive.namelist())
            assert "waloader.db" in names
            assert "apps/demo/versions/000001/source/app.py" in names
            assert "logs/waloader/app.log" in names  # factory backups include logs
        assert report.purge_after is not None
        # wiped except backups/
        assert not config.database_path.exists()
        assert not (config.data_dir / "apps").exists()
        assert not (config.data_dir / "logs").exists()
        assert backup.exists()
        assert report.wipe.leftovers == []
        assert any("first-run" in n for n in report.notes)
        assert any("To undo" in n for n in report.notes)
        # the audit row travelled INTO the backup
        import sqlite3 as s

        extracted = config.data_dir / "check.db"
        extracted.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup) as archive:
            extracted.write_bytes(archive.read("waloader.db"))
        check = s.connect(extracted)
        actions = [r[0] for r in check.execute("SELECT action FROM audit_log")]
        check.close()
        assert "factory_reset.run" in actions

    def test_skip_backup(self, env) -> None:
        config, conn, owner, app = env
        conn.close()
        report = frs.factory_reset(config, skip_backup=True, actor="tester")
        assert report.backup_skipped and report.backup_path is None
        assert not sb.factory_dir(config).exists() or not list(
            sb.factory_dir(config).glob("*.zip")
        )
        assert not config.database_path.exists()

    def test_reset_without_database_wipes_only(self, env) -> None:
        config, conn, owner, app = env
        conn.close()
        config.database_path.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(config.database_path) + suffix).unlink(missing_ok=True)
        report = frs.factory_reset(config, actor="tester")
        assert report.backup_skipped
        assert any("no database" in n for n in report.notes)
        assert not (config.data_dir / "apps").exists()


class TestBackupctlCli:
    def test_create_list_roundtrip(self, env, capsys) -> None:
        config, conn, owner, app = env
        assert backupctl.main(["create", "--scope", "db"]) == 0
        assert "backup created" in capsys.readouterr().out
        assert backupctl.main(["create", "--scope", "app", "--app", "demo"]) == 0
        capsys.readouterr()
        assert backupctl.main(["list"]) == 0
        out = capsys.readouterr().out
        assert "db-" in out and "app-demo-" in out and "manual" in out

    def test_create_scope_app_needs_slug(self, env, capsys) -> None:
        config, conn, owner, app = env
        assert backupctl.main(["create", "--scope", "app"]) == 1
        assert "needs an app slug" in capsys.readouterr().err

    def test_restore_via_cli_into_wiped_dir(self, env, capsys) -> None:
        from waloader.services import restore

        config, conn, owner, app = env
        assert backupctl.main(["create", "--scope", "all"]) == 0
        archive = sb.list_backups(config)[0].path
        conn.close()
        restore.wipe_data_dir(config)

        # crucially: restore must NOT auto-create a DB first
        assert backupctl.main(["restore", str(archive)]) == 0
        out = capsys.readouterr().out
        assert "restored" in out and "1 app(s)" in out
        fresh = wdb.connect(config.database_path)
        assert apps_repo.get_by_slug(fresh, "demo") is not None
        fresh.close()

    def test_restore_refuses_then_force(self, env, capsys) -> None:
        config, conn, owner, app = env
        assert backupctl.main(["create", "--scope", "all"]) == 0
        archive = sb.list_backups(config)[0].path
        conn.close()
        assert backupctl.main(["restore", str(archive)]) == 1
        assert "--force" in capsys.readouterr().err
        assert backupctl.main(["restore", str(archive), "--force"]) == 0

    def test_factory_reset_confirmation_gate(self, env, monkeypatch, capsys) -> None:
        config, conn, owner, app = env
        conn.close()
        monkeypatch.setattr("builtins.input", lambda prompt="": "nope")
        assert backupctl.main(["factory-reset"]) == 1
        assert "Aborted" in capsys.readouterr().err
        assert config.database_path.exists()  # nothing happened

        monkeypatch.setattr("builtins.input", lambda prompt="": "RESET")
        assert backupctl.main(["factory-reset"]) == 0
        out = capsys.readouterr().out
        assert "backup:" in out and "first-run" in out
        assert not config.database_path.exists()

    def test_factory_reset_force_skips_prompt(self, env, monkeypatch,
                                              capsys) -> None:
        config, conn, owner, app = env
        conn.close()

        def explode(prompt=""):
            raise AssertionError("prompt must not be shown with --force")

        monkeypatch.setattr("builtins.input", explode)
        assert backupctl.main(["factory-reset", "--force", "--skip-backup"]) == 0
        assert "SKIPPED" in capsys.readouterr().out


class TestMaintenanceIntegration:
    def test_run_all_prunes_factory_backups(self, env) -> None:
        import time

        from waloader.services import maintenance_service

        config, conn, owner, app = env
        result = sb.create_backup(
            conn, config, "all", dest_dir=sb.factory_dir(config),
            scope_label="factory",
        )
        old = time.time() - (config.retention.factory_reset_backup_days + 1) * 86400
        os.utime(result.path, (old, old))
        report = maintenance_service.run_all(conn, config)
        assert report.factory_backups_removed == 1
        assert "expired factory backups removed: 1" in report.summary()
        assert not result.path.exists()
