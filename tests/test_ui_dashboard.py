"""UI tests for the dashboard card + gear dialog behaviors that broke in the
field: the confirmation must stay in the OPEN dialog (fragment-scoped rerun,
not a plain rerun that closes st.dialog), and enabling user management on an
app whose code lacks require_login must be surfaced, not silently ignored."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import waloader
from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo
from waloader.services import layout, security
from waloader.ui import page_dashboard
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
    yield config, conn, admin
    try:
        conn.close()
    except Exception:
        pass


def _app_with_source(conn, config, admin, *, source: str, user_mgmt: bool,
                     state: str = "running", slug: str = "app1"):
    app = apps_repo.create(conn, owner_id=admin.id, name="App One", slug=slug)
    src = layout.source_dir(config, slug, 1)
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.py").write_text(source, encoding="utf-8")
    versions_repo.create(
        conn, app_id=app.id, version_number=1, manifest={"entrypoint": "app.py"},
        bundle_path=f"apps/{slug}/versions/000001/uploaded_bundle.md",
        source_path=layout.relativize(config, src), created_by=admin.id,
    )
    apps_repo.set_current_version(conn, app.id, 1)
    apps_repo.set_state(conn, app.id, state)
    apps_repo.set_user_mgmt(conn, app.id, user_mgmt)
    conn.commit()
    return apps_repo.get(conn, app.id)


def _dashboard() -> None:
    from waloader.ui import page_dashboard as pd

    pd.render()


def _run(admin_id: int, **session) -> AppTest:
    at = AppTest.from_function(_dashboard, default_timeout=30)
    at.session_state[SESSION_USER_KEY] = admin_id
    for key, value in session.items():
        at.session_state[key] = value
    return at.run()


class TestConfirmHelpers:
    """The regression guard: setting/dismissing a confirmation must use a
    FRAGMENT-scoped rerun (a plain st.rerun closes the dialog)."""

    def test_request_confirm_sets_state_and_fragment_rerun(
        self, monkeypatch
    ) -> None:
        import streamlit as st

        calls = {}
        monkeypatch.setattr(
            st, "rerun", lambda scope="app": calls.setdefault("scope", scope)
        )
        store: dict = {}
        monkeypatch.setattr(page_dashboard.st, "session_state", store)

        page_dashboard._request_confirm(7, "restart")
        assert store[page_dashboard._confirm_key(7)] == "restart"
        assert calls["scope"] == "fragment"  # NOT "app" — that would close the dialog

    def test_dismiss_confirm_clears_state_and_fragment_rerun(
        self, monkeypatch
    ) -> None:
        import streamlit as st

        calls = {}
        monkeypatch.setattr(
            st, "rerun", lambda scope="app": calls.setdefault("scope", scope)
        )
        store = {page_dashboard._confirm_key(7): "restart"}
        monkeypatch.setattr(page_dashboard.st, "session_state", store)

        page_dashboard._dismiss_confirm(7)
        assert page_dashboard._confirm_key(7) not in store
        assert calls["scope"] == "fragment"


class TestGearDialogConfirmation:
    def test_confirmation_renders_in_open_dialog(self, env, monkeypatch) -> None:
        """With a pending action, opening the gear shows the Yes/Cancel
        confirmation (not the plain action buttons) — the dialog stays usable."""
        config, conn, admin = env
        from waloader.services import processes

        monkeypatch.setattr(processes, "is_app_running", lambda c, a: True)
        app = _app_with_source(conn, config, admin,
                               source="import streamlit as st\n", user_mgmt=False)

        at = _run(admin.id, **{page_dashboard._confirm_key(app.id): "restart"})
        at.button(key=f"gear_{app.id}").click()
        at = at.run()
        keys = {b.key for b in at.button}
        assert f"yes_{app.id}" in keys and f"cancel_{app.id}" in keys
        assert f"restart_{app.id}" not in keys  # action buttons replaced by confirm
        assert any("restart" in w.value for w in at.warning)

    def test_action_buttons_shown_without_pending(self, env, monkeypatch) -> None:
        config, conn, admin = env
        from waloader.services import processes

        monkeypatch.setattr(processes, "is_app_running", lambda c, a: True)
        app = _app_with_source(conn, config, admin,
                               source="import streamlit as st\n", user_mgmt=False)
        at = _run(admin.id)
        at.button(key=f"gear_{app.id}").click()
        at = at.run()
        keys = {b.key for b in at.button}
        assert f"restart_{app.id}" in keys and f"stop_{app.id}" in keys
        assert f"yes_{app.id}" not in keys


class TestLoginNotEnforcedWarning:
    def test_warning_when_user_mgmt_on_but_code_lacks_login(self, env) -> None:
        config, conn, admin = env
        _app_with_source(conn, config, admin,
                         source="import streamlit as st\nst.title('x')\n",
                         user_mgmt=True)
        at = _run(admin.id)
        captions = " ".join(c.value for c in at.caption)
        assert "login ON but not enforced" in captions

    def test_no_warning_when_code_enforces_login(self, env) -> None:
        config, conn, admin = env
        _app_with_source(
            conn, config, admin,
            source="from waloader_sdk.auth import require_login\nrequire_login()\n",
            user_mgmt=True,
        )
        at = _run(admin.id)
        captions = " ".join(c.value for c in at.caption)
        assert "login ON but not enforced" not in captions

    def test_no_warning_when_user_mgmt_off(self, env) -> None:
        config, conn, admin = env
        _app_with_source(conn, config, admin,
                         source="import streamlit as st\n", user_mgmt=False)
        at = _run(admin.id)
        captions = " ".join(c.value for c in at.caption)
        assert "login ON but not enforced" not in captions


class TestBuildBadge:
    def test_build_info_reads_deploy_manifest(self, monkeypatch, tmp_path) -> None:
        """A deployed box (no git) shows the version+SHA+date baked into
        .deploy/manifest.json by the deploy tool."""
        from waloader.ui import common

        (tmp_path / ".deploy").mkdir()
        (tmp_path / ".deploy" / "manifest.json").write_text(
            '{"waloader_version": "1.2.3", "git_sha": "abc1234", '
            '"created_at": "2026-07-07T10:00:00Z"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(common, "_project_root", lambda: tmp_path)
        info = common.build_info()
        assert "abc1234" in info and "2026-07-07" in info

    def test_build_info_falls_back_to_live_git(self, monkeypatch, tmp_path) -> None:
        from waloader.ui import common

        monkeypatch.setattr(common, "_project_root", lambda: tmp_path)  # no manifest

        class _R:
            returncode = 0
            stdout = "deadbee\n"

        monkeypatch.setattr(common.subprocess, "run", lambda *a, **k: _R())
        info = common.build_info()
        assert "deadbee-dev" in info

    def test_build_info_version_only_last_resort(self, monkeypatch, tmp_path) -> None:
        from waloader.ui import common

        monkeypatch.setattr(common, "_project_root", lambda: tmp_path)

        def boom(*a, **k):
            raise OSError("no git")

        monkeypatch.setattr(common.subprocess, "run", boom)
        assert common.build_info() == f"v{waloader.__version__}"

    def test_render_build_badge_emits_fixed_corner_markup(self) -> None:
        def script() -> None:
            from waloader.ui import common

            common.render_build_badge()

        at = AppTest.from_function(script, default_timeout=15).run()
        body = " ".join(m.value for m in at.markdown)
        assert "position:fixed" in body and "bottom:6px" in body
        assert f"v{waloader.__version__}" in body

    def test_app_entrypoint_renders_the_badge(self) -> None:
        # guard that the entrypoint actually calls it (unit test can't run the
        # full navigation app cleanly)
        source = Path("src/waloader/ui/app.py").read_text(encoding="utf-8")
        assert "render_build_badge()" in source


class TestGearAlignment:
    def test_header_columns_top_aligned(self, env) -> None:
        """Guard the gear-centering fix: the card header row is top-aligned so
        the gear sits next to the first line of a wrapping app name."""
        config, conn, admin = env
        app = _app_with_source(conn, config, admin,
                               source="import streamlit as st\n", user_mgmt=False)
        at = _run(admin.id)
        # the header uses st.columns(..., vertical_alignment="top"); assert the
        # source encodes that (AppTest can't measure pixels)
        source = Path("src/waloader/ui/page_dashboard.py").read_text(encoding="utf-8")
        assert 'vertical_alignment="top"' in source
        assert at.button(key=f"gear_{app.id}")  # gear present on the card
