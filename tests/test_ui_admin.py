"""AppTest coverage for the admin panel pages (P11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from waloader import db as wdb
from waloader.config import apply_db_overrides, load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import settings as settings_repo
from waloader.repositories import users as users_repo
from waloader.services import security
from waloader.ui.common import SESSION_USER_KEY


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(f'[paths]\ndata_dir = "{tmp_path / "data"}"\n', encoding="utf-8")
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    admin = users_repo.create(conn, "root", "r@x.com",
                              security.hash_password("root-pw-1234"), is_admin=True)
    conn.commit()
    yield config, conn, admin
    conn.close()


def _settings_script() -> None:
    from waloader.ui import page_admin_settings

    page_admin_settings.render()


def _processes_script() -> None:
    from waloader.ui import page_admin_processes

    page_admin_processes.render()


def _caddy_script() -> None:
    from waloader.ui import page_admin_caddy

    page_admin_caddy.render()


def _run(script, user_id: int) -> AppTest:
    at = AppTest.from_function(script, default_timeout=20)
    at.session_state[SESSION_USER_KEY] = user_id
    return at.run()


class TestSettingsPanel:
    def test_non_admin_blocked(self, env) -> None:
        config, conn, admin = env
        regular = users_repo.create(conn, "reg", "", security.hash_password("x" * 10))
        conn.commit()
        at = _run(_settings_script, regular.id)
        assert at.exception

    def test_edit_setting_persists_as_db_override(self, env) -> None:
        config, conn, admin = env
        at = _run(_settings_script, admin.id)
        at.number_input(key="set_health.interval_seconds").set_value(60)
        at = at.run()
        # each section form has its own Save; find the one in [health]
        health_form_saves = [b for b in at.button if b.label == "Save"]
        # click every Save is wasteful; the health one is enough — locate by form
        target = next(b for b in health_form_saves
                      if getattr(b, "form_id", "form_health") == "form_health")
        target.click()
        at = at.run()
        assert settings_repo.get(conn, "health.interval_seconds") == 60
        effective = apply_db_overrides(load_config(), settings_repo.get_all(conn))
        assert effective.config.health.interval_seconds == 60
        assert effective.source_of("health.interval_seconds") == "db"

    def test_invalid_value_rejected(self, env) -> None:
        config, conn, admin = env
        at = _run(_settings_script, admin.id)
        at.number_input(key="set_ports.child_app_end").set_value(1)  # < start
        at = at.run()
        saves = [b for b in at.button if b.label == "Save"]
        target = next(b for b in saves
                      if getattr(b, "form_id", "form_ports") == "form_ports")
        target.click()
        at = at.run()
        assert any("child_app_end" in e.value for e in at.error)
        assert settings_repo.get(conn, "ports.child_app_end") is None  # not saved

    def test_paths_shown_readonly(self, env) -> None:
        config, conn, admin = env
        at = _run(_settings_script, admin.id)
        editable_keys = [(t.key or "") for t in at.text_input]
        editable_keys += [(n.key or "") for n in at.number_input]
        assert not [k for k in editable_keys if k.startswith("set_paths.")]
        assert [k for k in editable_keys if k.startswith("set_ports.")]  # editable ones exist


class TestProcessesPanel:
    def test_reconcile_flow_with_resume_candidate(self, env) -> None:
        config, conn, admin = env
        app = apps_repo.create(conn, owner_id=admin.id, name="Dead", slug="dead")
        apps_repo.set_state(conn, app.id, "running")
        runtime_repo.upsert_started(conn, app.id, pid=2_111_111, pid_create_time=1.0)
        conn.commit()

        at = _run(_processes_script, admin.id)
        [b for b in at.button if b.label == "Run reconciliation"][0].click()
        at = at.run()
        body = " ".join(w.value for w in at.subheader)
        assert "Reconciliation result" in body
        assert "Resume previously running apps" in body
        assert apps_repo.get(conn, app.id).state == "stopped"


class TestCaddyPanel:
    def test_status_and_generate(self, env) -> None:
        config, conn, admin = env
        at = _run(_caddy_script, admin.id)
        body = " ".join(m.value for m in at.markdown)
        assert "not running" in body
        assert any("Caddy is disabled" in i.value for i in at.info)
        [b for b in at.button if b.label == "Generate"][0].click()
        at = at.run()
        assert config.caddy_config_path.exists()
        assert any("caddyfile written" in s.value for s in at.success)
