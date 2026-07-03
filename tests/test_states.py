from __future__ import annotations

import sqlite3

import pytest

from waloader.models import App
from waloader.repositories import apps as apps_repo
from waloader.services import states


class TestMatrix:
    def test_full_allowed_matrix(self) -> None:
        expected_allowed = {
            ("created", "deploying"), ("created", "pending_delete"),
            ("deploying", "running"), ("deploying", "deployment_failed"),
            ("deploying", "stopped"),
            ("deployment_failed", "deploying"), ("deployment_failed", "pending_delete"),
            ("running", "stopped"), ("running", "failed"), ("running", "deploying"),
            ("running", "pending_delete"),
            ("stopped", "running"), ("stopped", "deploying"), ("stopped", "pending_delete"),
            ("failed", "running"), ("failed", "stopped"), ("failed", "deploying"),
            ("failed", "pending_delete"),
            ("pending_delete", "deleted"),
        }
        for src in states.ALL_STATES:
            for dst in states.ALL_STATES:
                if src == dst:
                    continue
                assert states.can_transition(src, dst) == (
                    (src, dst) in expected_allowed
                ), f"{src} -> {dst}"

    def test_deleted_is_terminal(self) -> None:
        for dst in states.ALL_STATES:
            assert not states.can_transition("deleted", dst) or dst == "deleted"


class TestTransition:
    def test_apply(self, conn: sqlite3.Connection, app: App) -> None:
        updated = states.transition(conn, app, states.DEPLOYING)
        assert updated.state == states.DEPLOYING
        assert apps_repo.get(conn, app.id).state == states.DEPLOYING

    def test_same_state_is_noop(self, conn: sqlite3.Connection, app: App) -> None:
        assert states.transition(conn, app, states.CREATED).state == states.CREATED

    def test_forbidden_raises(self, conn: sqlite3.Connection, app: App) -> None:
        with pytest.raises(states.InvalidTransitionError, match="cannot go"):
            states.transition(conn, app, states.RUNNING)  # created -> running

    def test_unknown_state_raises(self, conn: sqlite3.Connection, app: App) -> None:
        with pytest.raises(states.InvalidTransitionError, match="Unknown state"):
            states.transition(conn, app, "exploded")

    def test_rereads_current_state(self, conn: sqlite3.Connection, app: App) -> None:
        apps_repo.set_state(conn, app.id, states.RUNNING)  # moved behind our back
        conn.commit()
        updated = states.transition(conn, app, states.STOPPED)  # running -> stopped ok
        assert updated.state == states.STOPPED
