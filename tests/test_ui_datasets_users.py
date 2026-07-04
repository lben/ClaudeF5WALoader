"""AppTest coverage for the P10 pages (datasets mapping, app users, admin users)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import apps as apps_repo
from waloader.repositories import datasets as datasets_repo
from waloader.repositories import users as users_repo
from waloader.services import app_users_service as aus
from waloader.services import datasets_service, security
from waloader.ui.common import SESSION_USER_KEY


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(f'[paths]\ndata_dir = "{tmp_path / "data"}"\n', encoding="utf-8")
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "owner", "o@x.com",
                              security.hash_password("owner-pw-123"))
    app = apps_repo.create(conn, owner_id=owner.id, name="Demo", slug="demo")
    conn.commit()
    yield config, conn, owner, app
    conn.close()


def _datasets_script() -> None:
    from waloader.ui import page_datasets

    page_datasets.render()


def _app_users_script() -> None:
    from waloader.ui import page_app_users

    page_app_users.render()


def _admin_users_script() -> None:
    from waloader.ui import page_admin_users

    page_admin_users.render()


def _run(script, user_id: int) -> AppTest:
    at = AppTest.from_function(script, default_timeout=15)
    at.session_state[SESSION_USER_KEY] = user_id
    return at.run()


def _fill(at: AppTest, label: str, value: str) -> None:
    """Fill a text input by label (element order flattens column-wise)."""
    next(t for t in at.text_input if t.label == label).input(value)


class TestDatasetsPage:
    def test_add_concept(self, env) -> None:
        config, conn, owner, app = env
        at = _run(_datasets_script, owner.id)
        at.text_input[0].input("clients")
        at = at.run()
        [b for b in at.button if b.label == "Save"][0].click()
        at = at.run()
        assert datasets_repo.get_concept_by_name(conn, app.id, "clients") is not None
        body = " ".join(m.value for m in at.markdown)
        assert "clients" in body
        assert "No data uploaded yet" in body  # italic empty state

    def test_invalid_concept_name_shows_error(self, env) -> None:
        config, conn, owner, app = env
        at = _run(_datasets_script, owner.id)
        at.text_input[0].input("Bad Name!")
        at = at.run()
        [b for b in at.button if b.label == "Save"][0].click()
        at = at.run()
        assert any("Concept names" in e.value for e in at.error)

    def test_delete_concept_needs_confirmation(self, env) -> None:
        config, conn, owner, app = env
        concept = datasets_service.create_concept(conn, app, "clients")
        at = _run(_datasets_script, owner.id)
        [b for b in at.button if b.label == "Delete"][0].click()
        at = at.run()
        # still there until confirmed
        assert datasets_repo.get_concept_by_name(conn, app.id, "clients") is not None
        assert any("Delete concept" in w.value for w in at.warning)
        [b for b in at.button if b.label == "Yes, delete"][0].click()
        at = at.run()
        assert datasets_repo.get_concept_by_name(conn, app.id, "clients") is None
        assert concept.id  # silence unused warning

    def test_current_file_summary_shown(self, env) -> None:
        config, conn, owner, app = env
        concept = datasets_service.create_concept(conn, app, "clients")
        datasets_service.store_upload(
            conn, config, app, concept, filename="c.csv", data=b"id\n1\n2\n"
        )
        at = _run(_datasets_script, owner.id)
        captions = " ".join(c.value for c in at.caption)
        assert "current: c.csv" in captions


class TestAppUsersPage:
    def test_create_and_list(self, env) -> None:
        config, conn, owner, app = env
        at = _run(_app_users_script, owner.id)
        _fill(at, "Username", "jdoe")
        _fill(at, "Email", "j@corp.com")
        _fill(at, "Password", "proper-pw-123")
        _fill(at, "Observations", "CFO approved")
        at = at.run()
        [b for b in at.button if b.label == "Create user"][0].click()
        at = at.run()
        created = app_users_repo.get_by_username(conn, app.id, "jdoe")
        assert created is not None and created.observations == "CFO approved"
        assert created.email == "j@corp.com"
        # the password really is the password (argon2 verify through the service)
        aus.authenticate_app_user(conn, app, "jdoe", "proper-pw-123")

    def test_weak_password_error(self, env) -> None:
        config, conn, owner, app = env
        at = _run(_app_users_script, owner.id)
        _fill(at, "Username", "jdoe")
        _fill(at, "Password", "pw")
        at = at.run()
        [b for b in at.button if b.label == "Create user"][0].click()
        at = at.run()
        assert any("at least 8" in e.value for e in at.error)

    def test_toggle_user_management(self, env) -> None:
        config, conn, owner, app = env
        at = _run(_app_users_script, owner.id)
        toggle = at.toggle(key=f"aum_toggle_{app.id}")
        toggle.set_value(True)
        at = at.run()
        assert apps_repo.get(conn, app.id).user_mgmt_enabled == 1

    def test_deactivate_existing_user(self, env) -> None:
        config, conn, owner, app = env
        created = aus.create_app_user(conn, app, username="jdoe", email="",
                                      password="proper-pw-123")
        at = _run(_app_users_script, owner.id)
        at.button(key=f"deact_{created.id}").click()
        at = at.run()
        assert app_users_repo.get(conn, created.id).is_active == 0


class TestAdminUsersPage:
    def test_non_admin_blocked(self, env) -> None:
        config, conn, owner, app = env
        at = _run(_admin_users_script, owner.id)
        assert at.exception  # NotAuthorizedError surfaces

    def test_admin_creates_user(self, env) -> None:
        config, conn, owner, app = env
        admin = users_repo.create(conn, "root", "r@x.com",
                                  security.hash_password("root-pw-1234"),
                                  is_admin=True)
        conn.commit()
        at = _run(_admin_users_script, admin.id)
        _fill(at, "Username", "newbie")
        _fill(at, "Email", "n@x.com")
        _fill(at, "Password", "newbie-pw-123")
        at = at.run()
        [b for b in at.button if b.label == "Create user"][0].click()
        at = at.run()
        created = users_repo.get_by_username(conn, "newbie")
        assert created is not None and created.email == "n@x.com"

    def test_admin_deactivates_other(self, env) -> None:
        config, conn, owner, app = env
        admin = users_repo.create(conn, "root", "r@x.com",
                                  security.hash_password("root-pw-1234"),
                                  is_admin=True)
        conn.commit()
        at = _run(_admin_users_script, admin.id)
        at.button(key=f"pdeact_{owner.id}").click()
        at = at.run()
        assert users_repo.get(conn, owner.id).is_active == 0
