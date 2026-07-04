"""UI tests via streamlit.testing.v1.AppTest (headless script runs, no browser)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.services import security, users_service
from waloader.ui.common import SESSION_USER_KEY

APP_PATH = str(Path("src/waloader/ui/app.py").resolve())


@pytest.fixture
def ui_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[health]\nbackground_enabled = false\n",  # deterministic tests
        encoding="utf-8",
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    return load_config().config


@pytest.fixture
def seeded_admin(ui_env):
    conn = wdb.connect(ui_env.database_path)
    wdb.migrate(conn)
    admin = users_repo.create(
        conn, "admin", "a@x.com", security.hash_password("admin-pw-123"),
        is_admin=True,
    )
    conn.commit()
    yield conn, admin
    conn.close()


def _run_app(session_state: dict | None = None) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    for key, value in (session_state or {}).items():
        at.session_state[key] = value
    return at.run()


class TestGate:
    def test_first_run_shows_bootstrap(self, ui_env) -> None:
        at = _run_app()
        assert any("First-time setup" in s.value for s in at.subheader)

    def test_bootstrap_creates_admin_and_enters(self, ui_env) -> None:
        at = _run_app()
        at.text_input[0].input("admin")
        at.text_input[1].input("a@x.com")
        at.text_input[2].input("admin-pw-123")
        at.text_input[3].input("admin-pw-123")
        at = at.run()
        form_buttons = [b for b in at.button if b.label == "Create administrator"]
        form_buttons[0].click()
        at = at.run()
        # authenticated now: dashboard renders
        assert at.session_state[SESSION_USER_KEY] is not None
        assert any("Your apps" in h.value for h in at.header)

    def test_login_wrong_then_right(self, seeded_admin, ui_env) -> None:
        at = _run_app()
        assert any("Log in" in s.value for s in at.subheader)
        at.text_input[0].input("admin")
        at.text_input[1].input("wrong-password")
        at = at.run()
        [b for b in at.button if b.label == "Log in"][0].click()
        at = at.run()
        assert any("Invalid username or password" in e.value for e in at.error)

        at.text_input[0].input("admin")
        at.text_input[1].input("admin-pw-123")
        at = at.run()
        [b for b in at.button if b.label == "Log in"][0].click()
        at = at.run()
        assert any("Your apps" in h.value for h in at.header)


class TestDashboard:
    def test_empty_state(self, seeded_admin) -> None:
        _, admin = seeded_admin
        at = _run_app({SESSION_USER_KEY: admin.id})
        assert any("No apps yet" in i.value for i in at.info)

    def test_cards_render(self, seeded_admin, ui_env) -> None:
        conn, admin = seeded_admin
        app = apps_repo.create(conn, owner_id=admin.id, name="Positions",
                               slug="positions", description="desk view")
        # NB: a "running" state with no live process would be reconciled to
        # stopped by boot_once — which is correct. Seed an honest stopped app.
        apps_repo.set_state(conn, app.id, "stopped")
        apps_repo.set_port(conn, app.id, 48123)
        apps_repo.set_current_version(conn, app.id, 2)
        conn.commit()

        at = _run_app({SESSION_USER_KEY: admin.id})
        body = " ".join(m.value for m in at.markdown)
        assert "Positions" in body
        assert "⚪ stopped" in body
        captions = " ".join(c.value for c in at.caption)
        assert "v2" in captions and "port 48123" in captions

    def test_dead_running_app_reconciled_at_boot(self, seeded_admin, ui_env) -> None:
        conn, admin = seeded_admin
        app = apps_repo.create(conn, owner_id=admin.id, name="Zombie", slug="zombie")
        apps_repo.set_state(conn, app.id, "running")  # but no live process
        conn.commit()
        _run_app({SESSION_USER_KEY: admin.id})
        assert apps_repo.get(conn, app.id).state == "stopped"

    def test_other_owners_apps_hidden_by_default(self, seeded_admin, ui_env) -> None:
        conn, admin = seeded_admin
        other = users_service.create_user(conn, username="other", email="",
                                          password="x" * 10)
        apps_repo.create(conn, owner_id=other.id, name="Foreign", slug="foreign")
        conn.commit()
        at = _run_app({SESSION_USER_KEY: admin.id})
        body = " ".join(m.value for m in at.markdown)
        assert "Foreign" not in body  # admin sees it only with the toggle


def _create_page_script() -> None:
    # self-contained: from_function runs this source standalone
    from waloader.ui import page_create

    page_create.render()


def _account_page_script() -> None:
    from waloader.ui import page_account

    page_account.render()


class TestCreatePage:
    def _open_create(self, admin_id: int) -> AppTest:
        at = AppTest.from_function(_create_page_script, default_timeout=15)
        at.session_state[SESSION_USER_KEY] = admin_id
        return at.run()

    def test_availability_feedback(self, seeded_admin, ui_env) -> None:
        conn, admin = seeded_admin
        apps_repo.create(conn, owner_id=admin.id, name="Taken", slug="taken")
        conn.commit()

        at = self._open_create(admin.id)
        at.text_input[0].input("Fresh Name")
        at = at.run()
        body = " ".join(m.value for m in at.markdown)
        assert "✅ Available" in body and "fresh-name" in body

        at.text_input[0].input("taken")
        at = at.run()
        body = " ".join(m.value for m in at.markdown)
        assert "❌ Not available" in body
        assert any("already taken" in c.value for c in at.caption)

    def test_reserved_name_feedback(self, seeded_admin) -> None:
        _, admin = seeded_admin
        at = self._open_create(admin.id)
        at.text_input[0].input("admin")
        at = at.run()
        assert any("reserved" in c.value for c in at.caption)


class TestAccountPage:
    def _open_account(self, admin_id: int) -> AppTest:
        at = AppTest.from_function(_account_page_script, default_timeout=15)
        at.session_state[SESSION_USER_KEY] = admin_id
        return at.run()

    def test_change_password(self, seeded_admin) -> None:
        conn, admin = seeded_admin
        at = self._open_account(admin.id)
        assert any("Account" in h.value for h in at.header)
        at.text_input[0].input("admin-pw-123")
        at.text_input[1].input("brand-new-pw-1")
        at.text_input[2].input("brand-new-pw-1")
        at = at.run()
        [b for b in at.button if b.label == "Change password"][0].click()
        at = at.run()
        assert any("Password changed" in s.value for s in at.success)
        users_service.authenticate(conn, "admin", "brand-new-pw-1")

    def test_mismatch_rejected(self, seeded_admin) -> None:
        _, admin = seeded_admin
        at = self._open_account(admin.id)
        at.text_input[0].input("admin-pw-123")
        at.text_input[1].input("one-password-1")
        at.text_input[2].input("different-pw-2")
        at = at.run()
        [b for b in at.button if b.label == "Change password"][0].click()
        at = at.run()
        assert any("do not match" in e.value for e in at.error)
