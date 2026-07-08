"""Human-flow tests for the create-app screen. Field bugs guarded here:
the create screen must NEVER show a stale deploy outcome from a previous
deploy, and a create attempt must land the user on the dashboard (where the
outcome lives), not sit on the create form."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import users as users_repo
from waloader.services import health, processes, security
from waloader.ui import page_create
from waloader.ui.common import SESSION_DEPLOY_OUTCOME, SESSION_USER_KEY

GOOD_BUNDLE = (
    b"```toml waloader-bundle\n"
    b"bundle_format = 1\n"
    b'entrypoint = "app.py"\n'
    b"```\n"
    b"## file: app.py\n```python\nimport streamlit as st\n```\n"
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[ports]\nchild_app_start = 47870\nchild_app_end = 47890\n"
        "[health]\nbackground_enabled = false\ninitial_check_timeout_seconds = 1\n",
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
    for app in apps_repo.list_all(conn, include_deleted=True):
        processes.stop_app(conn, config, app)
    try:
        conn.close()
    except Exception:
        pass


def _ok_uv(command, env, timeout, cwd=None):
    from waloader.services import uv_env

    if len(command) > 2 and command[1] == "venv":
        py = uv_env.venv_python(Path(command[2]))
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _launcher(conn, config, app, version):
    pid, ct = processes.spawn_detached(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=Path.cwd(), env=dict(os.environ),
        log_file=config.logs_dir / "sleeper.log",
    )
    runtime_repo.upsert_started(conn, app.id, pid=pid, pid_create_time=ct)
    conn.commit()
    return pid, ct


def _healthy(config, app, process_alive):
    return health.ProbeResult(True, "")


SEAMS = {"_uv_runner": _ok_uv, "_launcher": _launcher, "_prober": _healthy}


class TestSubmitNewApp:
    def test_creates_deploys_and_stores_outcome(self, env, monkeypatch) -> None:
        config, conn, admin = env
        store = {}
        monkeypatch.setattr(page_create.st, "session_state", store)

        app, result = page_create.submit_new_app(
            config, admin, name="Sales Dashboard", description="d",
            user_mgmt=False, bundle_bytes=GOOD_BUNDLE, **SEAMS,
        )
        assert result.ok, result.error_block()
        assert apps_repo.get_by_slug(conn, "sales-dashboard") is not None
        # the outcome was stored for the dashboard to show
        assert store[SESSION_DEPLOY_OUTCOME]["app_name"] == "Sales Dashboard"
        assert store[SESSION_DEPLOY_OUTCOME]["ok"] is True

    def test_new_create_overwrites_previous_outcome(self, env, monkeypatch) -> None:
        """The user's bug: a previous app's success message lingered. Creating a
        new app must replace the stored outcome, not keep the old one."""
        config, conn, admin = env
        store = {SESSION_DEPLOY_OUTCOME: {
            "ok": True, "app_id": 999, "app_name": "PreviousApp",
            "kind": "update", "url": "http://x", "error_block": None,
            "error_summary": None,
        }}
        monkeypatch.setattr(page_create.st, "session_state", store)

        page_create.submit_new_app(
            config, admin, name="Fresh App", description="",
            user_mgmt=False, bundle_bytes=GOOD_BUNDLE, **SEAMS,
        )
        assert store[SESSION_DEPLOY_OUTCOME]["app_name"] == "Fresh App"  # not PreviousApp

    def test_duplicate_name_raises_before_deploy(self, env, monkeypatch) -> None:
        config, conn, admin = env
        monkeypatch.setattr(page_create.st, "session_state", {})
        page_create.submit_new_app(config, admin, name="Dup", description="",
                                   user_mgmt=False, bundle_bytes=GOOD_BUNDLE, **SEAMS)
        with pytest.raises(page_create.deployment.AppCreationError, match="taken"):
            page_create.submit_new_app(config, admin, name="dup", description="",
                                       user_mgmt=False, bundle_bytes=GOOD_BUNDLE,
                                       **SEAMS)


class TestCreatePageNoStaleOutcome:
    def test_create_screen_never_renders_a_deploy_outcome(self, env) -> None:
        """Regression: the create page used to render the shared deploy-outcome
        panel, so a previous app's 'deployed successfully' showed here."""
        config, conn, admin = env

        def script() -> None:
            from waloader.ui import page_create as pc

            pc.render()

        at = AppTest.from_function(script, default_timeout=20)
        at.session_state[SESSION_USER_KEY] = admin.id
        at.session_state[SESSION_DEPLOY_OUTCOME] = {
            "ok": True, "app_id": 1, "app_name": "ClientsManagementTest",
            "kind": "update", "url": "http://host/apps/x",
            "error_block": None, "error_summary": None,
        }
        at = at.run()
        body = " ".join(
            [m.value for m in at.markdown]
            + [s.value for s in at.success]
            + [c.value for c in at.caption]
        )
        assert "ClientsManagementTest" not in body
        assert "deployed successfully" not in body
        assert "Create new app" in " ".join(h.value for h in at.header)

    def test_create_redirects_to_dashboard(self) -> None:
        # the file-uploader widget can't be driven by AppTest, so guard the
        # redirect at the source: a create attempt calls nav.switch("dashboard")
        source = Path("src/waloader/ui/page_create.py").read_text(encoding="utf-8")
        assert 'nav.switch("dashboard")' in source
        # ...and the create page no longer renders the deploy outcome panel
        assert "render_deploy_outcome" not in source
