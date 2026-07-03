from __future__ import annotations

import sqlite3

import pytest

from waloader.models import App, User
from waloader.repositories import app_users as app_users_repo
from waloader.repositories import approvals as approvals_repo
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import datasets as datasets_repo
from waloader.repositories import deployments as deployments_repo
from waloader.repositories import notifications as notif_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import settings as settings_repo
from waloader.repositories import users as users_repo
from waloader.repositories import versions as versions_repo


class TestUsers:
    def test_create_and_lookup(self, conn: sqlite3.Connection) -> None:
        user = users_repo.create(conn, "bob", "bob@example.com", "h", is_admin=False)
        assert users_repo.get(conn, user.id).username == "bob"
        assert users_repo.get_by_username(conn, "bob").id == user.id
        assert users_repo.get_by_username(conn, "BOB").id == user.id  # case-insensitive
        assert users_repo.get_by_username(conn, "nobody") is None
        assert users_repo.count(conn) == 1

    def test_username_unique_case_insensitive(self, conn: sqlite3.Connection) -> None:
        users_repo.create(conn, "Carol", "c@example.com", "h")
        with pytest.raises(sqlite3.IntegrityError):
            users_repo.create(conn, "carol", "c2@example.com", "h")

    def test_mutations(self, conn: sqlite3.Connection, user: User) -> None:
        users_repo.set_password_hash(conn, user.id, "new-hash")
        users_repo.set_active(conn, user.id, False)
        users_repo.update_profile(conn, user.id, email="new@example.com")
        reloaded = users_repo.get(conn, user.id)
        assert reloaded.password_hash == "new-hash"
        assert reloaded.is_active == 0
        assert reloaded.email == "new@example.com"


class TestApps:
    def test_create_and_uniqueness(self, conn: sqlite3.Connection, user: User) -> None:
        app = apps_repo.create(conn, owner_id=user.id, name="My App", slug="my-app")
        assert apps_repo.get_by_slug(conn, "my-app").id == app.id
        assert apps_repo.name_taken(conn, "MY APP")
        assert apps_repo.slug_taken(conn, "MY-APP")
        assert not apps_repo.name_taken(conn, "Other")
        assert apps_repo.name_taken(conn, "My App", exclude_app_id=app.id) is False

    def test_soft_delete_keeps_slug_reserved(self, conn: sqlite3.Connection, app: App) -> None:
        apps_repo.mark_deleted(
            conn, app.id, archive_path="/a.zip", purge_after="2027-01-01T00:00:00+00:00"
        )
        assert apps_repo.get_by_slug(conn, app.slug).state == "deleted"
        assert apps_repo.slug_taken(conn, app.slug)
        assert apps_repo.list_all(conn) == []
        assert len(apps_repo.list_all(conn, include_deleted=True)) == 1

    def test_purge_due_and_hard_delete(self, conn: sqlite3.Connection, app: App) -> None:
        apps_repo.mark_deleted(
            conn, app.id, archive_path="/a.zip", purge_after="2026-01-01T00:00:00+00:00"
        )
        due = apps_repo.list_purge_due(conn, "2026-06-01T00:00:00+00:00")
        assert [a.id for a in due] == [app.id]
        assert apps_repo.list_purge_due(conn, "2025-12-01T00:00:00+00:00") == []
        apps_repo.hard_delete(conn, app.id)
        assert apps_repo.get_by_slug(conn, app.slug) is None
        assert not apps_repo.slug_taken(conn, app.slug)

    def test_setters(self, conn: sqlite3.Connection, app: App) -> None:
        apps_repo.set_state(conn, app.id, "running")
        apps_repo.set_port(conn, app.id, 8601)
        apps_repo.set_current_version(conn, app.id, 3)
        apps_repo.set_user_mgmt(conn, app.id, True)
        apps_repo.set_caddy_route(conn, app.id, "/apps/client-positions")
        apps_repo.set_last_deploy_error(conn, app.id, "boom")
        reloaded = apps_repo.get(conn, app.id)
        assert (
            reloaded.state,
            reloaded.port,
            reloaded.current_version,
            reloaded.user_mgmt_enabled,
            reloaded.caddy_route,
            reloaded.last_deploy_error,
        ) == ("running", 8601, 3, 1, "/apps/client-positions", "boom")
        assert apps_repo.used_ports(conn) == {8601}


class TestVersions:
    def test_numbering_and_lookup(self, conn: sqlite3.Connection, app: App) -> None:
        assert versions_repo.next_version_number(conn, app.id) == 1
        v1 = versions_repo.create(
            conn, app_id=app.id, version_number=1, manifest={"files": ["app.py"]},
            bundle_path="b1.md", source_path="s1", created_by=app.owner_id,
        )
        assert versions_repo.next_version_number(conn, app.id) == 2
        assert v1.manifest == {"files": ["app.py"]}
        assert versions_repo.get_by_number(conn, app.id, 1).id == v1.id
        assert versions_repo.get_by_number(conn, app.id, 9) is None
        assert len(versions_repo.list_for_app(conn, app.id)) == 1

    def test_duplicate_number_rejected(self, conn: sqlite3.Connection, app: App) -> None:
        versions_repo.create(
            conn, app_id=app.id, version_number=1, manifest={}, bundle_path="b",
            source_path="s", created_by=None,
        )
        with pytest.raises(sqlite3.IntegrityError):
            versions_repo.create(
                conn, app_id=app.id, version_number=1, manifest={}, bundle_path="b",
                source_path="s", created_by=None,
            )


class TestDeployments:
    def test_lifecycle(self, conn: sqlite3.Connection, app: App) -> None:
        dep = deployments_repo.start(conn, app_id=app.id, kind="create", log_path="deploy.log")
        assert dep.status == "in_progress"
        deployments_repo.finish(conn, dep.id, status="failed", error_summary="tests failed")
        reloaded = deployments_repo.get(conn, dep.id)
        assert reloaded.status == "failed"
        assert reloaded.error_summary == "tests failed"
        assert reloaded.finished_at is not None
        assert len(deployments_repo.list_for_app(conn, app.id)) == 1


class TestRuntime:
    def test_upsert_and_health_records(self, conn: sqlite3.Connection, app: App) -> None:
        runtime_repo.upsert_started(conn, app.id, pid=123, pid_create_time=1.5)
        rt = runtime_repo.get(conn, app.id)
        assert (rt.pid, rt.pid_create_time, rt.consecutive_failures) == (123, 1.5, 0)

        runtime_repo.record_healthy(conn, app.id)
        assert runtime_repo.get(conn, app.id).last_healthy_at is not None

        assert runtime_repo.record_unhealthy(conn, app.id, "port closed") == 1
        assert runtime_repo.record_unhealthy(conn, app.id, "port closed") == 2
        rt = runtime_repo.get(conn, app.id)
        assert rt.last_failure_reason == "port closed"

        runtime_repo.record_healthy(conn, app.id)
        assert runtime_repo.get(conn, app.id).consecutive_failures == 0

        runtime_repo.set_deployed_healthy(conn, app.id, True)
        runtime_repo.clear_process(conn, app.id)
        rt = runtime_repo.get(conn, app.id)
        assert rt.pid is None and rt.deployed_healthy == 1

    def test_restart_resets_flags(self, conn: sqlite3.Connection, app: App) -> None:
        runtime_repo.upsert_started(conn, app.id, pid=1, pid_create_time=1.0)
        runtime_repo.set_deployed_healthy(conn, app.id, True)
        runtime_repo.record_unhealthy(conn, app.id, "x")
        runtime_repo.upsert_started(conn, app.id, pid=2, pid_create_time=2.0)
        rt = runtime_repo.get(conn, app.id)
        assert (rt.pid, rt.deployed_healthy, rt.consecutive_failures) == (2, 0, 0)
        assert rt.last_failure_reason is None


class TestSettings:
    def test_roundtrip(self, conn: sqlite3.Connection) -> None:
        settings_repo.set_value(conn, "ports.waloader_port", 9000)
        settings_repo.set_value(conn, "uploads.allowed_dataset_extensions", [".csv"])
        assert settings_repo.get(conn, "ports.waloader_port") == 9000
        assert settings_repo.get_all(conn) == {
            "ports.waloader_port": 9000,
            "uploads.allowed_dataset_extensions": [".csv"],
        }
        settings_repo.set_value(conn, "ports.waloader_port", 9001)  # upsert
        assert settings_repo.get(conn, "ports.waloader_port") == 9001
        settings_repo.delete(conn, "ports.waloader_port")
        assert settings_repo.get(conn, "ports.waloader_port") is None


class TestNotifications:
    def test_dedupe(self, conn: sqlite3.Connection, app: App) -> None:
        key = "crash:2026-07-03T00:00:00+00:00"
        assert not notif_repo.was_sent(conn, app.id, key)
        notif_repo.mark_sent(conn, app.id, key)
        notif_repo.mark_sent(conn, app.id, key)  # idempotent
        assert notif_repo.was_sent(conn, app.id, key)
        notif_repo.clear_for_app(conn, app.id)
        assert not notif_repo.was_sent(conn, app.id, key)


class TestDatasets:
    def test_concepts_and_files(self, conn: sqlite3.Connection, app: App) -> None:
        concept = datasets_repo.create_concept(conn, app.id, "clients")
        assert datasets_repo.get_concept_by_name(conn, app.id, "clients").id == concept.id
        with pytest.raises(sqlite3.IntegrityError):
            datasets_repo.create_concept(conn, app.id, "clients")

        f1 = datasets_repo.add_file(
            conn, concept_id=concept.id, original_filename="c.xlsx", original_path="o1",
            canonical_path="c1.parquet", sheet_name="Sheet1",
            schema={"id": "int64"}, size_bytes=10, uploaded_by=None,
        )
        assert f1.is_current == 1 and f1.sheet_name == "Sheet1"
        f2 = datasets_repo.add_file(
            conn, concept_id=concept.id, original_filename="c2.csv", original_path="o2",
            canonical_path="c2.parquet", sheet_name=None,
            schema={"id": "int64", "name": "object"}, size_bytes=20, uploaded_by=None,
        )
        current = datasets_repo.current_file(conn, concept.id)
        assert current.id == f2.id and current.schema == {"id": "int64", "name": "object"}
        assert [f.id for f in datasets_repo.file_history(conn, concept.id)] == [f2.id, f1.id]

        datasets_repo.delete_concept(conn, concept.id)
        assert datasets_repo.list_concepts(conn, app.id) == []
        assert datasets_repo.current_file(conn, concept.id) is None  # cascade


class TestAppUsers:
    def test_crud_and_attachments(self, conn: sqlite3.Connection, app: App) -> None:
        au = app_users_repo.create(
            conn, app_id=app.id, username="jdoe", email="j@example.com", password_hash="h"
        )
        assert app_users_repo.get_by_username(conn, app.id, "JDOE").id == au.id
        with pytest.raises(sqlite3.IntegrityError):
            app_users_repo.create(
                conn, app_id=app.id, username="JDoe", email="", password_hash="h"
            )

        app_users_repo.update(conn, au.id, email="new@example.com", observations="VIP access")
        app_users_repo.set_active(conn, au.id, False)
        app_users_repo.set_password_hash(conn, au.id, "h2")
        reloaded = app_users_repo.get(conn, au.id)
        assert (reloaded.email, reloaded.observations, reloaded.is_active,
                reloaded.password_hash) == ("new@example.com", "VIP access", 0, "h2")

        att = app_users_repo.add_attachment(
            conn, app_user_id=au.id, filename="grant.png", stored_path="/p/grant.png",
            note="approval screenshot",
        )
        assert [a.id for a in app_users_repo.list_attachments(conn, au.id)] == [att.id]
        app_users_repo.delete(conn, au.id)
        assert app_users_repo.list_attachments(conn, au.id) == []  # cascade


class TestApprovals:
    def test_approve_and_check(self, conn: sqlite3.Connection, app: App) -> None:
        assert not approvals_repo.is_approved(conn, app.id, "numpy>=2")
        approvals_repo.approve(conn, app.id, "numpy>=2", approved_by=None)
        approvals_repo.approve(conn, app.id, "numpy>=2", approved_by=None)  # idempotent
        assert approvals_repo.is_approved(conn, app.id, "numpy>=2")
        assert approvals_repo.list_for_app(conn, app.id) == ["numpy>=2"]


class TestAudit:
    def test_record_and_read(self, conn: sqlite3.Connection) -> None:
        audit_repo.record(
            conn, actor="alice", action="app.create", target="my-app", details={"port": 8601}
        )
        rows = audit_repo.recent(conn)
        assert len(rows) == 1
        assert rows[0]["action"] == "app.create"
