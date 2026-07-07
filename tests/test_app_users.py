from __future__ import annotations

import sqlite3

import pytest

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import apps as apps_repo
from waloader.services import app_users_service as aus
from waloader.services import layout, security
from waloader.services.users_service import AuthError


@pytest.fixture
def app_user(conn: sqlite3.Connection, app: App):
    return aus.create_app_user(
        conn, app, username="jdoe", email="j@corp.com",
        password="app-user-pw1", observations="approved by CFO",
    )


class TestToggle:
    def test_enable_disable(self, conn, app: App) -> None:
        assert not aus.login_required(app)
        app = aus.set_user_management(conn, app, True, actor="alice")
        assert aus.login_required(app)
        app = aus.set_user_management(conn, app, False, actor="alice")
        assert not aus.login_required(app)


class TestCodeEnforcesLogin:
    """Detect apps that turned on user mgmt but whose code never calls the
    login SDK (WALoader can't inject login) — the field bug where enabling the
    toggle silently did nothing."""

    def _version_with_source(self, conn, config, app, source_text: str) -> None:
        src = layout.source_dir(config, app.slug, 1)
        src.mkdir(parents=True, exist_ok=True)
        (src / "app.py").write_text(source_text, encoding="utf-8")
        from waloader.repositories import versions as versions_repo

        versions_repo.create(
            conn, app_id=app.id, version_number=1,
            manifest={"entrypoint": "app.py"},
            bundle_path=f"apps/{app.slug}/versions/000001/uploaded_bundle.md",
            source_path=layout.relativize(config, src), created_by=app.owner_id,
        )
        apps_repo.set_current_version(conn, app.id, 1)
        conn.commit()

    def test_false_when_code_lacks_require_login(self, conn, config, app) -> None:
        self._version_with_source(conn, config, app,
                                  "import streamlit as st\nst.title('hi')\n")
        assert aus.code_enforces_login(config, apps_repo.get(conn, app.id)) is False

    def test_true_when_code_calls_require_login(self, conn, config, app) -> None:
        self._version_with_source(
            conn, config, app,
            "from waloader_sdk.auth import require_login\nrequire_login()\n",
        )
        assert aus.code_enforces_login(config, apps_repo.get(conn, app.id)) is True

    def test_true_via_module_reference(self, conn, config, app) -> None:
        self._version_with_source(
            conn, config, app, "import waloader_sdk.auth as a\na.require_login()\n"
        )
        assert aus.code_enforces_login(config, apps_repo.get(conn, app.id)) is True

    def test_false_without_a_version(self, conn, config, app) -> None:
        assert aus.code_enforces_login(config, app) is False  # current_version None


class TestCrud:
    def test_create(self, conn, app: App, app_user) -> None:
        assert app_user.username == "jdoe"
        assert app_user.observations == "approved by CFO"
        assert app_user.password_hash.startswith("$argon2")

    def test_validations(self, conn, app: App, app_user) -> None:
        with pytest.raises(aus.AppUserError, match="already exists"):
            aus.create_app_user(conn, app, username="JDOE", email="",
                                password="x" * 10)
        with pytest.raises(aus.AppUserError, match="Username"):
            aus.create_app_user(conn, app, username="bad user!", email="",
                                password="x" * 10)
        with pytest.raises(security.WeakPasswordError):
            aus.create_app_user(conn, app, username="ok", email="", password="pw")

    def test_same_username_across_apps_is_fine(self, conn, app: App, user,
                                               app_user) -> None:
        other = apps_repo.create(conn, owner_id=user.id, name="Other", slug="other")
        conn.commit()
        created = aus.create_app_user(conn, other, username="jdoe", email="",
                                      password="x" * 10)
        assert created.app_id == other.id

    def test_update_deactivate_reactivate(self, conn, app: App, app_user) -> None:
        aus.update_app_user(conn, app, app_user.id, email="new@corp.com",
                            observations="moved desks")
        aus.set_app_user_active(conn, app, app_user.id, False)
        reloaded = app_users_repo.get(conn, app_user.id)
        assert (reloaded.email, reloaded.observations, reloaded.is_active) == (
            "new@corp.com", "moved desks", 0,
        )
        aus.set_app_user_active(conn, app, app_user.id, True)
        assert app_users_repo.get(conn, app_user.id).is_active == 1

    def test_delete_removes_files(self, conn, config: WALoaderConfig, app: App,
                                  app_user) -> None:
        aus.add_attachment(conn, config, app, app_user.id,
                           filename="grant.png", data=b"png-bytes")
        files_dir = layout.user_files_dir(config, app.slug, app_user.id)
        assert files_dir.exists()
        aus.delete_app_user(conn, config, app, app_user.id)
        assert not files_dir.exists()
        assert app_users_repo.list_for_app(conn, app.id) == []


class TestAuthentication:
    def test_ok(self, conn, app: App, app_user) -> None:
        user = aus.authenticate_app_user(conn, app, "jdoe", "app-user-pw1")
        assert user.id == app_user.id

    def test_wrong_password(self, conn, app: App, app_user) -> None:
        with pytest.raises(AuthError, match="Invalid"):
            aus.authenticate_app_user(conn, app, "jdoe", "nope")

    def test_inactive(self, conn, app: App, app_user) -> None:
        aus.set_app_user_active(conn, app, app_user.id, False)
        with pytest.raises(AuthError, match="deactivated"):
            aus.authenticate_app_user(conn, app, "jdoe", "app-user-pw1")

    def test_change_password(self, conn, app: App, app_user) -> None:
        with pytest.raises(AuthError, match="Current password"):
            aus.change_app_user_password(conn, app, app_user.id, "wrong", "new-pw-123")
        aus.change_app_user_password(conn, app, app_user.id, "app-user-pw1",
                                     "new-pw-123")
        aus.authenticate_app_user(conn, app, "jdoe", "new-pw-123")

    def test_owner_reset(self, conn, app: App, app_user) -> None:
        aus.owner_reset_app_user_password(conn, app, app_user.id, "reset-pw-99",
                                          actor="alice")
        aus.authenticate_app_user(conn, app, "jdoe", "reset-pw-99")


class TestAttachments:
    def test_add_list_delete(self, conn, config: WALoaderConfig, app: App,
                             app_user) -> None:
        att = aus.add_attachment(conn, config, app, app_user.id,
                                 filename="access grant.png", data=b"img",
                                 note="screenshot of approval email")
        stored = layout.resolve(config, att.stored_path)
        assert stored.read_bytes() == b"img"
        assert att.note == "screenshot of approval email"

        # same filename never overwrites
        att2 = aus.add_attachment(conn, config, app, app_user.id,
                                  filename="access grant.png", data=b"img2")
        assert layout.resolve(config, att2.stored_path) != stored

        assert len(app_users_repo.list_attachments(conn, app_user.id)) == 2
        aus.delete_attachment(conn, config, app, att.id)
        assert not stored.exists()
        assert len(app_users_repo.list_attachments(conn, app_user.id)) == 1

    def test_empty_filename_rejected(self, conn, config, app: App, app_user) -> None:
        with pytest.raises(aus.AppUserError, match="filename"):
            aus.add_attachment(conn, config, app, app_user.id,
                               filename="  ", data=b"x")


class TestSdkAuth:
    """The pure (non-streamlit) core of waloader_sdk.auth."""

    def test_login_required_flag(self, conn, app: App) -> None:
        from waloader_sdk import auth as sdk_auth

        assert not sdk_auth.login_required(conn, app.slug)
        aus.set_user_management(conn, app, True)
        assert sdk_auth.login_required(conn, app.slug)

    def test_login_required_unknown_app(self, conn) -> None:
        from waloader_sdk import auth as sdk_auth

        with pytest.raises(sdk_auth.AuthError, match="not registered"):
            sdk_auth.login_required(conn, "ghost")

    def test_authenticate_compatible_with_service_hashes(self, conn, app: App,
                                                         app_user) -> None:
        from waloader_sdk import auth as sdk_auth

        user = sdk_auth.authenticate(conn, app.slug, "jdoe", "app-user-pw1")
        assert user["id"] == app_user.id and user["email"] == "j@corp.com"
        with pytest.raises(sdk_auth.AuthError, match="Invalid"):
            sdk_auth.authenticate(conn, app.slug, "jdoe", "wrong")

    def test_authenticate_inactive(self, conn, app: App, app_user) -> None:
        from waloader_sdk import auth as sdk_auth

        aus.set_app_user_active(conn, app, app_user.id, False)
        with pytest.raises(sdk_auth.AuthError, match="deactivated"):
            sdk_auth.authenticate(conn, app.slug, "jdoe", "app-user-pw1")

    def test_sdk_change_password_roundtrip(self, conn, app: App, app_user) -> None:
        from waloader_sdk import auth as sdk_auth

        with pytest.raises(sdk_auth.AuthError, match="Current password"):
            sdk_auth.change_password(conn, app_user.id, "bad", "whatever-new")
        with pytest.raises(sdk_auth.AuthError, match="at least 8"):
            sdk_auth.change_password(conn, app_user.id, "app-user-pw1", "short")
        sdk_auth.change_password(conn, app_user.id, "app-user-pw1", "sdk-new-pw1")
        # the platform service accepts the SDK-written hash
        aus.authenticate_app_user(conn, app, "jdoe", "sdk-new-pw1")
