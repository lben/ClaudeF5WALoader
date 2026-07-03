from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from waloader import db
from waloader.config import LoadedConfig, WALoaderConfig
from waloader.models import App, User
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    db.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def user(conn: sqlite3.Connection) -> User:
    created = users_repo.create(conn, "alice", "alice@example.com", "hash", is_admin=True)
    conn.commit()
    return created


@pytest.fixture
def app(conn: sqlite3.Connection, user: User) -> App:
    created = apps_repo.create(
        conn, owner_id=user.id, name="Client Positions", slug="client-positions"
    )
    conn.commit()
    return created


@pytest.fixture
def config(tmp_path: Path) -> WALoaderConfig:
    return WALoaderConfig.model_validate({"paths": {"data_dir": str(tmp_path / "data")}})


@pytest.fixture
def loaded_config(config: WALoaderConfig) -> LoadedConfig:
    return LoadedConfig(config=config)
