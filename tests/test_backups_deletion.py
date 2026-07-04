from __future__ import annotations

import os
import sqlite3
import time
import zipfile
from pathlib import Path

from waloader import db
from waloader.config import WALoaderConfig
from waloader.models import User
from waloader.repositories import apps as apps_repo
from waloader.repositories import versions as versions_repo
from waloader.services import backups, deletion, layout, maintenance_service


def _config(tmp_path: Path, **retention) -> WALoaderConfig:
    return WALoaderConfig.model_validate(
        {"paths": {"data_dir": str(tmp_path / "data")},
         "retention": retention or {}}
    )


def _real_db(config: WALoaderConfig) -> sqlite3.Connection:
    conn = db.connect(config.database_path)
    db.migrate(conn)
    return conn


class TestBackups:
    def test_backup_then_unchanged_then_changed(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        conn = _real_db(config)

        first = backups.backup_database(config)
        assert first.created and first.path.exists()
        assert first.path.with_name(first.path.name + ".sha256").exists()

        second = backups.backup_database(config)
        assert not second.created and "unchanged" in second.reason

        conn.execute("INSERT INTO settings (key, value_json, updated_at) "
                     "VALUES ('x', '1', 'now')")
        conn.commit()
        third = backups.backup_database(config)
        assert third.created and third.path != first.path
        conn.close()

    def test_backup_missing_db(self, tmp_path: Path) -> None:
        result = backups.backup_database(_config(tmp_path))
        assert not result.created and "does not exist" in result.reason

    def test_cleanup_backups_by_age(self, tmp_path: Path) -> None:
        config = _config(tmp_path, backup_days=30)
        conn = _real_db(config)
        conn.close()
        result = backups.backup_database(config)
        old_time = time.time() - 40 * 86400
        os.utime(result.path, (old_time, old_time))
        os.utime(result.path.with_name(result.path.name + ".sha256"),
                 (old_time, old_time))
        removed = backups.cleanup_backups(config)
        assert len(removed) == 2
        assert not result.path.exists()

    def test_cleanup_logs_by_age(self, tmp_path: Path) -> None:
        config = _config(tmp_path, log_days=30)
        old_log = config.logs_dir / "apps" / "x" / "000001" / "runtime.log"
        old_log.parent.mkdir(parents=True)
        old_log.write_text("old")
        fresh_log = config.logs_dir / "waloader" / "app.log"
        fresh_log.parent.mkdir(parents=True)
        fresh_log.write_text("fresh")
        old_time = time.time() - 40 * 86400
        os.utime(old_log, (old_time, old_time))

        assert backups.cleanup_logs(config) == 1
        assert not old_log.exists()
        assert fresh_log.exists()
        assert not old_log.parent.exists()  # empty dirs pruned


class TestDeletion:
    def _app_with_content(self, conn, config: WALoaderConfig, user: User):
        app = apps_repo.create(conn, owner_id=user.id, name="Doomed", slug="doomed")
        source = layout.source_dir(config, "doomed", 1)
        source.mkdir(parents=True)
        (source / "app.py").write_text("print('hi')\n")
        layout.bundle_path(config, "doomed", 1).write_text("bundle")
        versions_repo.create(
            conn, app_id=app.id, version_number=1, manifest={"entrypoint": "app.py"},
            bundle_path=layout.relativize(config, layout.bundle_path(config, "doomed", 1)),
            source_path=layout.relativize(config, source),
            created_by=user.id,
        )
        apps_repo.set_current_version(conn, app.id, 1)
        apps_repo.set_port(conn, app.id, 48001)
        # venv content must NOT be archived
        venv = layout.venv_dir(config, "doomed", 1)
        venv.mkdir(parents=True)
        (venv / "big-lib.so").write_text("binary")
        datasets = layout.concept_dir(config, "doomed", "clients")
        datasets.mkdir(parents=True)
        (datasets / "current.parquet").write_text("pq")
        conn.commit()
        return apps_repo.get(conn, app.id)

    def test_soft_delete_archives_and_hides(self, conn, tmp_path: Path,
                                            user: User) -> None:
        config = _config(tmp_path, deleted_app_days=183)
        app = self._app_with_content(conn, config, user)

        archive_path = deletion.soft_delete_app(conn, config, app, actor="alice")
        assert archive_path.exists()

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert "metadata.json" in names
            assert "doomed/versions/000001/source/app.py" in names
            assert "doomed/datasets/clients/current.parquet" in names
            assert not any("runtime" in n for n in names)  # venvs excluded

        reloaded = apps_repo.get_by_slug(conn, "doomed")
        assert reloaded.state == "deleted"
        assert reloaded.deleted_at is not None and reloaded.purge_after is not None
        assert apps_repo.list_all(conn) == []  # hidden from dashboards
        assert not layout.app_dir(config, "doomed").exists()  # disk freed
        assert apps_repo.slug_taken(conn, "doomed")  # slug still reserved

    def test_hard_delete_expired(self, conn, tmp_path: Path, user: User) -> None:
        config = _config(tmp_path, deleted_app_days=0)  # purge immediately
        app = self._app_with_content(conn, config, user)
        archive_path = deletion.soft_delete_app(conn, config, app)

        purged = deletion.hard_delete_expired(
            conn, config, now_iso="2099-01-01T00:00:00+00:00"
        )
        assert purged == ["doomed"]
        assert not archive_path.exists()
        assert apps_repo.get_by_slug(conn, "doomed") is None
        assert not apps_repo.slug_taken(conn, "doomed")  # name and port free again

    def test_hard_delete_respects_retention(self, conn, tmp_path: Path,
                                            user: User) -> None:
        config = _config(tmp_path, deleted_app_days=183)
        app = self._app_with_content(conn, config, user)
        deletion.soft_delete_app(conn, config, app)
        assert deletion.hard_delete_expired(conn, config) == []  # not yet due
        assert apps_repo.get_by_slug(conn, "doomed") is not None


class TestMaintenanceRunAll:
    def test_run_all_report(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        conn = _real_db(config)
        report = maintenance_service.run_all(conn, config)
        assert report.backup_created
        assert report.apps_purged == []
        assert "backup: created" in report.summary()
        second = maintenance_service.run_all(conn, config)
        assert not second.backup_created and "unchanged" in second.backup_reason
        conn.close()
