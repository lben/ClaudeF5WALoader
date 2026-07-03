from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from waloader import db


def test_connect_pragmas(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


def test_fresh_migrate_applies_all_then_none(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    applied = db.migrate(conn)
    assert [m.version for m in applied] == [1]
    assert db.migrate(conn) == []  # idempotent
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "schema_migrations", "users", "apps", "app_versions", "deployments", "app_runtime",
        "dataset_concepts", "dataset_files", "app_users", "app_user_attachments", "settings",
        "notifications_sent", "dependency_approvals", "audit_log",
    } <= tables
    conn.close()


def test_migration_status(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    before = db.migration_status(conn)
    assert all(not s.applied for s in before)
    db.migrate(conn)
    after = db.migration_status(conn)
    assert all(s.applied and s.applied_at for s in after)
    conn.close()


def test_incremental_migration(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_first.sql").write_text("CREATE TABLE t1 (id INTEGER PRIMARY KEY)")
    conn = db.connect(tmp_path / "x.db")
    assert [m.version for m in db.migrate(conn, migrations)] == [1]

    (migrations / "002_second.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);\nCREATE INDEX idx_t2 ON t2(id)"
    )
    assert [m.version for m in db.migrate(conn, migrations)] == [2]
    assert db.migrate(conn, migrations) == []
    conn.close()


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_bad.sql").write_text(
        "CREATE TABLE good (id INTEGER PRIMARY KEY);\nCREATE BROKEN SYNTAX"
    )
    conn = db.connect(tmp_path / "x.db")
    with pytest.raises(db.MigrationError):
        db.migrate(conn, migrations)
    assert db.applied_versions(conn) == {}
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "good" not in tables  # rolled back atomically
    conn.close()


def test_bad_migration_filename_rejected(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "1_first.sql").write_text("SELECT 1")
    with pytest.raises(db.MigrationError, match="NNN_name"):
        db.discover_migrations(migrations)


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO apps (owner_id, name, slug, created_at, updated_at)
               VALUES (999, 'X', 'x', '2026-01-01T00:00:00+00:00',
                       '2026-01-01T00:00:00+00:00')"""
        )
    conn.close()
