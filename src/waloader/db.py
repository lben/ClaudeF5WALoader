"""SQLite connection layer and SQL migrations framework.

Multiple processes share the DB (UI, CLI tools, child apps via the SDK), so
every connection gets WAL journaling, a busy timeout, and foreign keys ON.
Migrations are numbered ``NNN_name.sql`` files applied in order inside a
transaction each, tracked in ``schema_migrations``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from waloader.paths import ensure_dir
from waloader.util import utc_now_iso

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{3})_([A-Za-z0-9_\-]+)\.sql$")


class MigrationError(Exception):
    pass


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        ensure_dir(path.parent)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE transaction — used for read-modify-write sections."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def discover_migrations(migrations_dir: Path | None = None) -> list[Migration]:
    directory = migrations_dir or MIGRATIONS_DIR
    found: dict[int, Migration] = {}
    for entry in sorted(directory.glob("*.sql")):
        match = _MIGRATION_RE.match(entry.name)
        if not match:
            raise MigrationError(f"Migration filename not in NNN_name.sql form: {entry.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"Duplicate migration version {version:03d}")
        found[version] = Migration(version, match.group(2), entry)
    return [found[v] for v in sorted(found)]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               applied_at TEXT NOT NULL
           )"""
    )
    conn.commit()


def applied_versions(conn: sqlite3.Connection) -> dict[int, str]:
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT version, applied_at FROM schema_migrations").fetchall()
    return {row["version"]: row["applied_at"] for row in rows}


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> list[Migration]:
    """Apply all pending migrations in order. Returns those applied."""
    _ensure_migrations_table(conn)
    done = applied_versions(conn)
    applied: list[Migration] = []
    for migration in discover_migrations(migrations_dir):
        if migration.version in done:
            continue
        sql = migration.path.read_text(encoding="utf-8")
        try:
            with transaction(conn):
                for statement in _split_statements(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)",
                    (migration.version, migration.name, utc_now_iso()),
                )
        except sqlite3.Error as exc:
            raise MigrationError(
                f"Migration {migration.version:03d}_{migration.name} failed: {exc}"
            ) from exc
        applied.append(migration)
    return applied


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into statements (no triggers/procs in our schema)."""
    statements = []
    for chunk in sql.split(";"):
        stripped = chunk.strip()
        if stripped:
            statements.append(stripped)
    return statements


@dataclass(frozen=True)
class MigrationStatus:
    version: int
    name: str
    applied: bool
    applied_at: str | None


def migration_status(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> list[MigrationStatus]:
    done = applied_versions(conn)
    return [
        MigrationStatus(m.version, m.name, m.version in done, done.get(m.version))
        for m in discover_migrations(migrations_dir)
    ]
