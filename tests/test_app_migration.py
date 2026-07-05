from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from waloader import db as wdb
from waloader.config import WALoaderConfig
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import apps as apps_repo
from waloader.repositories import datasets as datasets_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo
from waloader.services import (
    app_migration,
    app_users_service,
    datasets_service,
    deployment,
    layout,
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
    "## file: app.py\n```python\nimport streamlit as st\n```\n"
)


@pytest.fixture
def env(tmp_path: Path):
    config = WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "ports": {"child_app_start": 47950, "child_app_end": 47970},
            "health": {"initial_check_timeout_seconds": 1},
        }
    )
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "owner", "o@x.com", "hash", is_admin=True)
    app = apps_repo.create(conn, owner_id=owner.id, name="Demo App", slug="demo-app",
                           description="the demo", user_mgmt_enabled=True)
    parsed = bundles_service.parse_bundle(BUNDLE_TEXT)
    versioning.create_version(conn, config, app, parsed, BUNDLE_TEXT.encode(),
                              created_by=owner.id)
    apps_repo.set_current_version(conn, app.id, 1)
    concept = datasets_service.create_concept(conn, app, "clients")
    datasets_service.store_upload(
        conn, config, app, concept, filename="c.csv",
        data=pd.DataFrame({"id": [1, 2]}).to_csv(index=False).encode(),
    )
    active = app_users_service.create_app_user(
        conn, app, username="jdoe", email="j@x.com", password="proper-pw-1",
        observations="approved by CFO",
    )
    app_users_service.add_attachment(conn, config, app, active.id,
                                     filename="grant.png", data=b"png-bytes")
    inactive = app_users_service.create_app_user(
        conn, app, username="gone", email="", password="proper-pw-2"
    )
    app_users_service.set_app_user_active(conn, app, inactive.id, False)
    conn.commit()
    yield config, conn, owner, apps_repo.get(conn, app.id)
    conn.close()


def _ok_uv(command, env, timeout, cwd=None):
    if len(command) > 2 and command[1] == "venv":
        python = uv_env.venv_python(Path(command[2]))
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


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


class TestExport:
    def test_export_lands_in_manual_dir(self, env) -> None:
        config, conn, owner, app = env
        path = app_migration.export_app(conn, config, app, actor="owner")
        assert path.parent == sb.manual_dir(config)
        assert path.name.startswith("app-demo-app-")

    def test_export_code_only(self, env) -> None:
        config, conn, owner, app = env
        path = app_migration.export_app(conn, config, app, include_data=False)
        with zipfile.ZipFile(path) as archive:
            assert not any("/datasets/" in n for n in archive.namelist())


class TestImportFidelity:
    def _exported(self, env) -> Path:
        config, conn, owner, app = env
        return app_migration.export_app(conn, config, app)

    def test_full_fidelity_without_deploy(self, env) -> None:
        config, conn, owner, app = env
        archive = self._exported(env)
        imported, result = app_migration.import_app(
            conn, config, archive, new_name="Demo App Two", deploy=False,
        )
        assert result is None
        assert imported.slug == "demo-app-two"
        assert imported.description == "the demo"
        assert imported.user_mgmt_enabled == 1
        assert imported.owner_id == owner.id  # archived owner exists locally
        assert imported.state == "created"
        assert imported.current_version == 1

        # versions: rewritten paths resolve; bundle byte-exact
        version = versions_repo.get_by_number(conn, imported.id, 1)
        assert version.bundle_path.startswith("apps/demo-app-two/")
        assert layout.resolve(config, version.bundle_path).read_bytes() == \
            BUNDLE_TEXT.encode()
        assert (layout.resolve(config, version.source_path) / "app.py").exists()

        # datasets: concept + current file with loadable parquet
        concept = datasets_repo.get_concept_by_name(conn, imported.id, "clients")
        current = datasets_repo.current_file(conn, concept.id)
        assert current.canonical_path.startswith("apps/demo-app-two/")
        df = pd.read_parquet(layout.resolve(config, current.canonical_path))
        assert list(df["id"]) == [1, 2]
        assert current.sheet_name is None

        # app users: argon2 hashes still verify; inactive preserved
        app_users_service.authenticate_app_user(conn, imported, "jdoe", "proper-pw-1")
        gone = app_users_repo.get_by_username(conn, imported.id, "gone")
        assert gone.is_active == 0
        jdoe = app_users_repo.get_by_username(conn, imported.id, "jdoe")
        assert jdoe.observations == "approved by CFO"
        (attachment,) = app_users_repo.list_attachments(conn, jdoe.id)
        assert layout.resolve(config, attachment.stored_path).read_bytes() == b"png-bytes"

        # rebuild is what's left
        assert deployment.needs_rebuild(config, imported)

    def test_import_with_deploy_seams(self, env) -> None:
        config, conn, owner, app = env
        archive = self._exported(env)
        imported, result = app_migration.import_app(
            conn, config, archive, new_name="Deployed Copy", **SEAMS,
        )
        assert result is not None and result.ok, result.error_block()
        assert result.kind == "rebuild"
        imported = apps_repo.get(conn, imported.id)
        assert imported.state == "running"
        from waloader.services import processes

        processes.stop_app(conn, config, imported)

    def test_code_only_archive_creates_concepts_without_files(self, env) -> None:
        config, conn, owner, app = env
        archive = app_migration.export_app(conn, config, app, include_data=False)
        imported, _ = app_migration.import_app(
            conn, config, archive, new_name="Code Only", deploy=False,
        )
        concept = datasets_repo.get_concept_by_name(conn, imported.id, "clients")
        assert concept is not None
        assert datasets_repo.current_file(conn, concept.id) is None
        assert app_users_repo.list_attachments(
            conn, app_users_repo.get_by_username(conn, imported.id, "jdoe").id
        ) == []


class TestImportValidation:
    def test_name_collision_hints_at_rename(self, env) -> None:
        config, conn, owner, app = env
        archive = app_migration.export_app(conn, config, app)
        with pytest.raises(app_migration.ImportAppError, match="--name"):
            app_migration.import_app(conn, config, archive, deploy=False)

    def test_owner_resolution(self, env) -> None:
        config, conn, owner, app = env
        archive = app_migration.export_app(conn, config, app)

        # unknown explicit owner
        with pytest.raises(app_migration.ImportAppError, match="No local user"):
            app_migration.import_app(conn, config, archive, owner_username="ghost",
                                     new_name="X1", deploy=False)

        # archived owner missing on this "instance": simulate by renaming
        users_repo.create(conn, "second", "", "h")
        conn.execute("UPDATE users SET username='renamed' WHERE username='owner'")
        conn.commit()
        with pytest.raises(app_migration.ImportAppError, match="pass --owner"):
            app_migration.import_app(conn, config, archive, new_name="X2",
                                     deploy=False)
        imported, _ = app_migration.import_app(
            conn, config, archive, owner_username="second", new_name="X3",
            deploy=False,
        )
        assert users_repo.get(conn, imported.owner_id).username == "second"

    def test_scope_backup_rejected_with_redirect(self, env) -> None:
        config, conn, owner, app = env
        backup = sb.create_backup(conn, config, "all")
        with pytest.raises(app_migration.ImportAppError, match="backupctl restore"):
            app_migration.import_app(conn, config, backup.path, deploy=False)

    def test_zip_slip_member_rejected(self, env, tmp_path: Path) -> None:
        import json

        config, conn, owner, app = env
        real = app_migration.export_app(conn, config, app)
        metadata = json.loads(zipfile.ZipFile(real).read("metadata.json"))
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("metadata.json", json.dumps(metadata))
            archive.writestr("demo-app/../../escape.txt", "boom")
        with pytest.raises(app_migration.ImportAppError, match="escapes the app dir"):
            app_migration.import_app(conn, config, evil, new_name="Evil",
                                     deploy=False)


class TestAppctlCli:
    def test_export_and_import_via_cli(self, env, monkeypatch, capsys) -> None:
        from waloader.tools import appctl

        config, conn, owner, app = env
        toml = Path(str(config.data_dir) + ".toml")
        toml.write_text(f'[paths]\ndata_dir = "{config.data_dir}"\n', encoding="utf-8")
        monkeypatch.setenv("WALOADER_CONFIG", str(toml))

        assert appctl.main(["export", "demo-app"]) == 0
        out = capsys.readouterr().out
        assert "exported: " in out
        archive = out.split("exported: ", 1)[1].strip()

        assert appctl.main(
            ["import", archive, "--name", "Via CLI", "--no-deploy"]
        ) == 0
        out = capsys.readouterr().out
        assert "imported as 'via-cli'" in out
        assert "rebuild" in out
        assert apps_repo.get_by_slug(conn, "via-cli") is not None

    def test_import_bad_archive_fails(self, env, monkeypatch, tmp_path) -> None:
        from waloader.tools import appctl

        config, conn, owner, app = env
        toml = tmp_path / "w.toml"
        toml.write_text(f'[paths]\ndata_dir = "{config.data_dir}"\n', encoding="utf-8")
        monkeypatch.setenv("WALOADER_CONFIG", str(toml))
        junk = tmp_path / "junk.zip"
        junk.write_text("nope")
        with pytest.raises(SystemExit):
            appctl.main(["import", str(junk)])
