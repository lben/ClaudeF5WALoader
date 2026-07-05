from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from waloader import db as wdb
from waloader.config import WALoaderConfig
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo
from waloader.services import app_archive, app_users_service, datasets_service, layout
from waloader.services import scoped_backups as sb


@pytest.fixture
def env(tmp_path: Path):
    """A real config-located DB with one fully-populated app."""
    config = WALoaderConfig.model_validate(
        {"paths": {"data_dir": str(tmp_path / "data")}}
    )
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "owner", "o@x.com", "hash", is_admin=True)
    app = apps_repo.create(conn, owner_id=owner.id, name="Demo App", slug="demo-app",
                           description="d", user_mgmt_enabled=True)

    # version with source + preserved bundle
    source = layout.source_dir(config, app.slug, 1)
    source.mkdir(parents=True)
    (source / "app.py").write_text("import streamlit\n")
    layout.bundle_path(config, app.slug, 1).write_text("THE BUNDLE BYTES")
    versions_repo.create(
        conn, app_id=app.id, version_number=1, manifest={"entrypoint": "app.py"},
        bundle_path=layout.relativize(config, layout.bundle_path(config, app.slug, 1)),
        source_path=layout.relativize(config, source), created_by=owner.id,
    )
    apps_repo.set_current_version(conn, app.id, 1)

    # venv content that must never be archived
    venv = layout.venv_dir(config, app.slug, 1)
    venv.mkdir(parents=True)
    (venv / "lib.so").write_text("binary")

    # dataset + app user + attachment
    concept = datasets_service.create_concept(conn, app, "clients")
    datasets_service.store_upload(
        conn, config, app, concept, filename="c.csv",
        data=pd.DataFrame({"id": [1]}).to_csv(index=False).encode(),
    )
    app_user = app_users_service.create_app_user(
        conn, app, username="jdoe", email="j@x.com", password="proper-pw-1",
        observations="approved",
    )
    app_users_service.add_attachment(conn, config, app, app_user.id,
                                     filename="grant.png", data=b"png")

    # a log file (for the with-logs switch)
    log_file = config.logs_dir / "apps" / app.slug / "000001" / "runtime.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("log line\n")

    conn.commit()
    yield config, conn, owner, apps_repo.get(conn, app.id)
    conn.close()


def _names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _member(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


class TestAppArchiveFormat2:
    def test_metadata_carries_everything_import_needs(self, env) -> None:
        config, conn, owner, app = env
        path = app_archive.build_app_archive(
            conn, config, app, include_data=True, dest_dir=config.tmp_dir
        )
        metadata = app_archive.read_metadata(path)
        assert metadata["archive_format"] == 2
        assert metadata["app"]["owner_username"] == "owner"
        assert metadata["app"]["user_mgmt_enabled"] == 1
        assert [v["version_number"] for v in metadata["versions"]] == [1]
        assert metadata["dataset_concepts"][0]["name"] == "clients"
        assert metadata["dataset_concepts"][0]["current_file"]["original_filename"] == "c.csv"
        (app_user,) = metadata["app_users"]
        assert app_user["username"] == "jdoe"
        assert app_user["password_hash"].startswith("$argon2")
        assert app_user["attachments"][0]["filename"] == "grant.png"

    def test_files_bundle_byte_exact_and_no_venv(self, env) -> None:
        config, conn, owner, app = env
        path = app_archive.build_app_archive(
            conn, config, app, include_data=True, dest_dir=config.tmp_dir
        )
        names = _names(path)
        assert "demo-app/versions/000001/source/app.py" in names
        assert "demo-app/datasets/clients/current.parquet" in names
        assert any(n.startswith("demo-app/user_files/") for n in names)
        assert not any("runtime" in n for n in names)
        assert _member(path, "demo-app/versions/000001/uploaded_bundle.md") == b"THE BUNDLE BYTES"

    def test_code_only_excludes_data(self, env) -> None:
        config, conn, owner, app = env
        path = app_archive.build_app_archive(
            conn, config, app, include_data=False, dest_dir=config.tmp_dir
        )
        names = _names(path)
        assert any(n.startswith("demo-app/versions/") for n in names)
        assert not any(n.startswith("demo-app/datasets/") for n in names)
        assert not any(n.startswith("demo-app/user_files/") for n in names)
        assert app_archive.read_metadata(path)["include_data"] is False

    def test_read_metadata_rejects_foreign_zip(self, env, tmp_path: Path) -> None:
        config, conn, owner, app = env
        foreign = tmp_path / "foreign.zip"
        with zipfile.ZipFile(foreign, "w") as archive:
            archive.writestr("metadata.json", json.dumps({"archive_format": 1}))
        with pytest.raises(app_archive.ArchiveError, match="Unsupported archive_format"):
            app_archive.read_metadata(foreign)
        not_a_zip = tmp_path / "x.zip"
        not_a_zip.write_text("nope")
        with pytest.raises(app_archive.ArchiveError, match="Not a readable"):
            app_archive.read_metadata(not_a_zip)

    def test_soft_delete_now_produces_format2(self, env) -> None:
        from waloader.services import deletion

        config, conn, owner, app = env
        archive_path = deletion.soft_delete_app(conn, config, app)
        metadata = app_archive.read_metadata(archive_path)
        assert metadata["archive_format"] == 2
        assert metadata["app_users"][0]["username"] == "jdoe"  # importable un-delete


class TestScopes:
    def test_db_scope(self, env) -> None:
        config, conn, owner, app = env
        result = sb.create_backup(conn, config, "db", actor="admin")
        names = _names(result.path)
        assert names == {"manifest.json", "waloader.db"}
        assert result.manifest["scope"] == "db"
        # the snapshot is a valid database with our data
        snapshot = config.tmp_dir / "check.db"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(_member(result.path, "waloader.db"))
        check = sqlite3.connect(snapshot)
        assert check.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        check.close()

    def test_all_scope_contents_and_exclusions(self, env) -> None:
        config, conn, owner, app = env
        # something under backups/ and tmp/ that must NOT be nested
        (sb.manual_dir(config)).mkdir(parents=True, exist_ok=True)
        (sb.manual_dir(config) / "old.zip").write_text("x")
        config.tmp_dir.mkdir(parents=True, exist_ok=True)
        (config.tmp_dir / "scratch").write_text("x")

        result = sb.create_backup(conn, config, "all", actor="admin")
        names = _names(result.path)
        assert "waloader.db" in names
        assert "apps/demo-app/versions/000001/source/app.py" in names
        assert "apps/demo-app/datasets/clients/current.parquet" in names
        assert not any(n.startswith("backups/") for n in names)
        assert not any(n.startswith("tmp/") for n in names)
        assert not any("runtime" in n for n in names)
        assert not any(n.startswith("logs/") for n in names)  # default: no logs
        assert result.path.parent == sb.manual_dir(config)

    def test_all_scope_with_logs(self, env) -> None:
        config, conn, owner, app = env
        result = sb.create_backup(conn, config, "all", include_logs=True)
        assert "logs/apps/demo-app/000001/runtime.log" in _names(result.path)

    def test_apps_scope_with_and_without_data(self, env) -> None:
        config, conn, owner, app = env
        with_data = sb.create_backup(conn, config, "apps", include_data=True)
        names = _names(with_data.path)
        assert "apps/demo-app/metadata.json" in names
        assert "apps/demo-app/datasets/clients/current.parquet" in names
        assert "waloader.db" not in names

        code_only = sb.create_backup(conn, config, "apps", include_data=False)
        names = _names(code_only.path)
        assert any(n.endswith("source/app.py") for n in names)
        assert not any("/datasets/" in n for n in names)

    def test_app_scope_is_importable_format(self, env) -> None:
        config, conn, owner, app = env
        result = sb.create_backup(conn, config, "app", app_slug="demo-app")
        assert result.path.name.startswith("app-demo-app-")
        assert app_archive.read_metadata(result.path)["app"]["slug"] == "demo-app"

    def test_bad_inputs(self, env) -> None:
        config, conn, owner, app = env
        with pytest.raises(sb.BackupError, match="Unknown backup scope"):
            sb.create_backup(conn, config, "everything")
        with pytest.raises(sb.BackupError, match="needs an app slug"):
            sb.create_backup(conn, config, "app")
        with pytest.raises(sb.BackupError, match="No app with slug"):
            sb.create_backup(conn, config, "app", app_slug="ghost")


class TestRegistry:
    def test_list_and_delete(self, env) -> None:
        config, conn, owner, app = env
        sb.create_backup(conn, config, "db")
        sb.create_backup(conn, config, "app", app_slug="demo-app")
        infos = sb.list_backups(config)
        assert len(infos) == 2
        assert {i.kind for i in infos} == {"manual"}
        assert all(i.size_bytes > 0 for i in infos)
        scopes = {i.scope for i in infos}
        assert "db" in scopes and "app" in scopes
        assert all(i.purge_after is None for i in infos)  # manual: never expires

        sb.delete_backup(config, infos[0].name)
        assert len(sb.list_backups(config)) == 1

    def test_factory_backups_show_purge_date_and_prune(self, env) -> None:
        import os
        import time

        config, conn, owner, app = env
        result = sb.create_backup(
            conn, config, "all", dest_dir=sb.factory_dir(config), scope_label="factory"
        )
        (info,) = sb.list_backups(config)
        assert info.kind == "factory" and info.purge_after is not None

        # age it past retention -> pruned
        old = time.time() - (config.retention.factory_reset_backup_days + 1) * 86400
        os.utime(result.path, (old, old))
        removed = sb.cleanup_factory_backups(config)
        assert removed == [result.path]
        assert sb.list_backups(config) == []

    def test_delete_guards_traversal(self, env) -> None:
        config, conn, owner, app = env
        with pytest.raises(sb.BackupError, match="Invalid backup name"):
            sb.delete_backup(config, "../waloader.db")
        with pytest.raises(sb.BackupError, match="No backup named"):
            sb.delete_backup(config, "ghost.zip")
