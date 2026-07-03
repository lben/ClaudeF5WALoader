from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from waloader.config import WALoaderConfig, _flatten, load_config
from waloader.models import User
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.services import authorization, security, users_service


class TestPasswordHashing:
    def test_roundtrip(self) -> None:
        h = security.hash_password("correct horse battery")
        assert h != "correct horse battery"
        assert h.startswith("$argon2")
        assert security.verify_password(h, "correct horse battery")
        assert not security.verify_password(h, "wrong")

    def test_garbage_hash_is_false_not_crash(self) -> None:
        assert not security.verify_password("not-a-hash", "pw")

    def test_strength(self) -> None:
        with pytest.raises(security.WeakPasswordError):
            security.validate_password_strength("short")
        security.validate_password_strength("longenough")


class TestUsersService:
    def test_create_and_authenticate(self, conn: sqlite3.Connection) -> None:
        user = users_service.create_user(
            conn, username="finance1", email="f1@corp.com", password="hunter22222"
        )
        assert user.is_admin == 0
        logged_in = users_service.authenticate(conn, "finance1", "hunter22222")
        assert logged_in.id == user.id

    def test_authenticate_failures(self, conn: sqlite3.Connection) -> None:
        users_service.create_user(
            conn, username="finance1", email="", password="hunter22222"
        )
        with pytest.raises(users_service.AuthError):
            users_service.authenticate(conn, "finance1", "wrong-password")
        with pytest.raises(users_service.AuthError):
            users_service.authenticate(conn, "ghost", "hunter22222")

    def test_inactive_cannot_login(self, conn: sqlite3.Connection) -> None:
        user = users_service.create_user(
            conn, username="finance1", email="", password="hunter22222"
        )
        users_service.set_active(conn, user.id, False, actor="admin")
        with pytest.raises(users_service.AuthError, match="deactivated"):
            users_service.authenticate(conn, "finance1", "hunter22222")

    def test_validation(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(users_service.UserValidationError, match="Username"):
            users_service.create_user(conn, username="bad name!", email="", password="x" * 10)
        with pytest.raises(security.WeakPasswordError):
            users_service.create_user(conn, username="ok", email="", password="short")
        users_service.create_user(conn, username="taken", email="", password="x" * 10)
        with pytest.raises(users_service.UserValidationError, match="already taken"):
            users_service.create_user(conn, username="TAKEN", email="", password="x" * 10)

    def test_change_password(self, conn: sqlite3.Connection) -> None:
        user = users_service.create_user(
            conn, username="finance1", email="", password="original-pw"
        )
        with pytest.raises(users_service.AuthError, match="Current password"):
            users_service.change_password(conn, user.id, "nope", "new-password")
        users_service.change_password(conn, user.id, "original-pw", "new-password")
        users_service.authenticate(conn, "finance1", "new-password")

    def test_admin_reset_password(self, conn: sqlite3.Connection) -> None:
        user = users_service.create_user(
            conn, username="finance1", email="", password="original-pw"
        )
        users_service.admin_reset_password(conn, user.id, "reset-by-admin", actor="admin")
        users_service.authenticate(conn, "finance1", "reset-by-admin")

    def test_audit_trail(self, conn: sqlite3.Connection) -> None:
        users_service.create_user(conn, username="finance1", email="", password="x" * 10)
        actions = [row["action"] for row in audit_repo.recent(conn)]
        assert "user.create" in actions


class TestBootstrap:
    def test_flow(self, conn: sqlite3.Connection) -> None:
        assert users_service.bootstrap_needed(conn)
        admin = users_service.bootstrap_admin(
            conn, username="admin", email="a@corp.com", password="admin-pw-123"
        )
        assert admin.is_admin == 1
        assert not users_service.bootstrap_needed(conn)
        with pytest.raises(users_service.UserValidationError, match="only allowed"):
            users_service.bootstrap_admin(
                conn, username="admin2", email="", password="admin-pw-123"
            )


class TestAuthorization:
    def test_admin_checks(self, conn: sqlite3.Connection, user: User) -> None:
        authorization.require_admin(user)  # fixture user is an active admin
        regular = users_service.create_user(
            conn, username="regular", email="", password="x" * 10
        )
        with pytest.raises(authorization.NotAuthorizedError):
            authorization.require_admin(regular)

    def test_app_manager_checks(self, conn: sqlite3.Connection, user: User) -> None:
        owner = users_service.create_user(conn, username="owner", email="", password="x" * 10)
        other = users_service.create_user(conn, username="other", email="", password="x" * 10)
        app = apps_repo.create(conn, owner_id=owner.id, name="A", slug="a")
        conn.commit()

        assert authorization.can_manage_app(owner, app)
        assert authorization.can_manage_app(user, app)  # admin
        assert not authorization.can_manage_app(other, app)
        with pytest.raises(authorization.NotAuthorizedError):
            authorization.require_app_manager(other, app)


class TestConfigDocsFramework:
    def test_example_toml_is_valid_and_complete(self) -> None:
        """The committed example must parse AND cover every setting exactly."""
        example = Path("config/waloader.example.toml")
        loaded = load_config(example)
        documented = set(loaded.sources)  # every key present in the example file
        actual = set(_flatten(WALoaderConfig().model_dump()))
        assert documented == actual, (
            f"example missing: {sorted(actual - documented)}; "
            f"stale: {sorted(documented - actual)}"
        )

    def test_example_toml_defaults_match_code(self) -> None:
        loaded = load_config(Path("config/waloader.example.toml"))
        assert loaded.config == WALoaderConfig()

    def test_docs_mention_every_setting(self) -> None:
        text = Path("docs/configuration.md").read_text(encoding="utf-8")
        for dotted in _flatten(WALoaderConfig().model_dump()):
            section, _, key = dotted.partition(".")
            assert key in text, f"docs/configuration.md does not document {dotted}"
