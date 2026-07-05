"""AppTest coverage for the Backups & reset admin page and rebuild indicator."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo
from waloader.services import scoped_backups as sb
from waloader.services import security
from waloader.ui.common import SESSION_USER_KEY


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[health]\nbackground_enabled = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    admin = users_repo.create(conn, "root", "r@x.com",
                              security.hash_password("root-pw-1234"), is_admin=True)
    conn.commit()
    yield config, conn, admin
    try:
        conn.close()
    except Exception:
        pass


def _backups_script() -> None:
    from waloader.ui import page_admin_backups

    page_admin_backups.render()


def _dashboard_script() -> None:
    from waloader.ui import page_dashboard

    page_dashboard.render()


def _run(script, user_id: int) -> AppTest:
    at = AppTest.from_function(script, default_timeout=20)
    at.session_state[SESSION_USER_KEY] = user_id
    return at.run()


class TestBackupsPage:
    def test_non_admin_blocked(self, env) -> None:
        config, conn, admin = env
        regular = users_repo.create(conn, "reg", "", security.hash_password("x" * 10))
        conn.commit()
        at = _run(_backups_script, regular.id)
        assert at.exception

    def test_create_db_backup_via_ui(self, env) -> None:
        config, conn, admin = env
        at = _run(_backups_script, admin.id)
        at.radio(key="backup_scope").set_value("Database only (admin data)")
        at = at.run()
        at.button(key="backup_create").click()
        at = at.run()
        backups = sb.list_backups(config)
        assert len(backups) == 1 and backups[0].scope == "db"
        assert any("Backup created" in s.value for s in at.success)

    def test_existing_backups_listed_and_delete_confirmed(self, env) -> None:
        config, conn, admin = env
        sb.create_backup(conn, config, "db")
        at = _run(_backups_script, admin.id)
        body = " ".join(m.value for m in at.markdown)
        assert "db-" in body and "manual" in body

        name = sb.list_backups(config)[0].name
        at.button(key=f"del_{name}").click()
        at = at.run()
        assert sb.list_backups(config)  # still there until confirmed
        assert any("permanently" in w.value for w in at.warning)
        at.button(key=f"yes_{name}").click()
        at = at.run()
        assert sb.list_backups(config) == []

    def test_factory_reset_gate_and_execution(self, env) -> None:
        config, conn, admin = env
        conn.close()

        # wrong confirmation text: click does nothing (server-side gate)
        at = _run(_backups_script, admin.id)
        at.text_input(key="reset_confirm_text").input("reset")
        at = at.run()
        at.button(key="factory_reset_go").click()
        at = at.run()
        assert config.database_path.exists()

        # correct confirmation: reset runs, report shown, data wiped
        at = _run(_backups_script, admin.id)
        at.text_input(key="reset_confirm_text").input("RESET")
        at = at.run()
        at.button(key="factory_reset_go").click()
        at = at.run()
        assert not config.database_path.exists()
        assert list(sb.factory_dir(config).glob("*.zip"))  # safety backup exists
        assert any("backup:" in s.value for s in at.success)
        assert any("first-run" in w.value for w in at.warning)


class TestRebuildIndicator:
    def test_card_shows_rebuild_required(self, env) -> None:
        config, conn, admin = env
        app = apps_repo.create(conn, owner_id=admin.id, name="Ghost", slug="ghost")
        versions_repo.create(
            conn, app_id=app.id, version_number=1, manifest={"entrypoint": "app.py"},
            bundle_path="apps/ghost/versions/000001/uploaded_bundle.md",
            source_path="apps/ghost/versions/000001/source", created_by=admin.id,
        )
        apps_repo.set_current_version(conn, app.id, 1)
        apps_repo.set_state(conn, app.id, "stopped")
        conn.commit()

        at = _run(_dashboard_script, admin.id)
        captions = " ".join(c.value for c in at.caption)
        assert "rebuild required" in captions
