from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import pytest

from waloader.config import WALoaderConfig
from waloader.models import User
from waloader.repositories import apps as apps_repo
from waloader.services import ports

# A range unlikely to collide with anything on a dev machine.
START, END = 47800, 47820


def _config(tmp_path: Path, start: int = START, end: int = END) -> WALoaderConfig:
    return WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "ports": {"child_app_start": start, "child_app_end": end},
        }
    )


def _app(conn: sqlite3.Connection, user: User, n: int):
    app = apps_repo.create(conn, owner_id=user.id, name=f"App {n}", slug=f"app-{n}")
    conn.commit()
    return app


class TestAllocation:
    def test_allocates_in_range_and_persists(
        self, conn: sqlite3.Connection, user: User, tmp_path: Path
    ) -> None:
        app = _app(conn, user, 1)
        port = ports.allocate_port(conn, _config(tmp_path), app.id)
        assert START <= port <= END
        assert apps_repo.get(conn, app.id).port == port

    def test_skips_db_reserved_ports(
        self, conn: sqlite3.Connection, user: User, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        first = ports.allocate_port(conn, config, _app(conn, user, 1).id)
        second = ports.allocate_port(conn, config, _app(conn, user, 2).id)
        assert first != second

    def test_stable_reuse(self, conn: sqlite3.Connection, user: User, tmp_path: Path) -> None:
        config = _config(tmp_path)
        app = _app(conn, user, 1)
        first = ports.allocate_port(conn, config, app.id)
        assert ports.allocate_port(conn, config, app.id) == first

    def test_skips_actually_occupied_port(
        self, conn: sqlite3.Connection, user: User, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("", START))
            blocker.listen(1)
            port = ports.allocate_port(conn, config, _app(conn, user, 1).id)
        assert port != START

    def test_reallocates_when_stale_port_is_taken_by_someone_else(
        self, conn: sqlite3.Connection, user: User, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        app = _app(conn, user, 1)
        first = ports.allocate_port(conn, config, app.id)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("", first))
            blocker.listen(1)
            # app is NOT running, and its old port is now occupied -> new port
            second = ports.allocate_port(conn, config, app.id)
        assert second != first

    def test_exhaustion(self, conn: sqlite3.Connection, user: User, tmp_path: Path) -> None:
        config = _config(tmp_path, start=START, end=START + 1)
        ports.allocate_port(conn, config, _app(conn, user, 1).id)
        ports.allocate_port(conn, config, _app(conn, user, 2).id)
        with pytest.raises(ports.PortAllocationError, match="No free port"):
            ports.allocate_port(conn, config, _app(conn, user, 3).id)

    def test_release(self, conn: sqlite3.Connection, user: User, tmp_path: Path) -> None:
        app = _app(conn, user, 1)
        ports.allocate_port(conn, _config(tmp_path), app.id)
        ports.release_port(conn, app.id)
        assert apps_repo.get(conn, app.id).port is None
