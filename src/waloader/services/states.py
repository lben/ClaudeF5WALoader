"""App runtime state machine — every state change goes through transition()."""

from __future__ import annotations

import sqlite3

from waloader.models import App
from waloader.repositories import apps as apps_repo

CREATED = "created"
DEPLOYING = "deploying"
DEPLOYMENT_FAILED = "deployment_failed"
RUNNING = "running"
STOPPED = "stopped"
FAILED = "failed"
DELETED = "deleted"
PENDING_DELETE = "pending_delete"

ALL_STATES = frozenset(
    {CREATED, DEPLOYING, DEPLOYMENT_FAILED, RUNNING, STOPPED, FAILED, DELETED, PENDING_DELETE}
)

# from-state -> allowed to-states
ALLOWED: dict[str, frozenset[str]] = {
    CREATED: frozenset({DEPLOYING, PENDING_DELETE}),
    DEPLOYING: frozenset({RUNNING, DEPLOYMENT_FAILED, STOPPED}),
    # STOPPED from DEPLOYING: update of a stopped app succeeded but stays stopped-by-user? No —
    # a successful deploy always launches; STOPPED is allowed for operator abort during deploy.
    DEPLOYMENT_FAILED: frozenset({DEPLOYING, PENDING_DELETE}),
    RUNNING: frozenset({STOPPED, FAILED, DEPLOYING, PENDING_DELETE}),
    STOPPED: frozenset({RUNNING, DEPLOYING, PENDING_DELETE}),
    FAILED: frozenset({RUNNING, STOPPED, DEPLOYING, PENDING_DELETE}),
    PENDING_DELETE: frozenset({DELETED}),
    DELETED: frozenset(),  # terminal; hard delete removes the row
}


class InvalidTransitionError(Exception):
    pass


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED.get(from_state, frozenset())


def transition(conn: sqlite3.Connection, app: App, to_state: str) -> App:
    """Validate and apply a state change; returns the refreshed app."""
    if to_state not in ALL_STATES:
        raise InvalidTransitionError(f"Unknown state {to_state!r}")
    current = apps_repo.get(conn, app.id)  # re-read: another process may have moved it
    if to_state == current.state:
        return current
    if not can_transition(current.state, to_state):
        raise InvalidTransitionError(
            f"App '{current.slug}': cannot go {current.state!r} -> {to_state!r}"
        )
    apps_repo.set_state(conn, current.id, to_state)
    conn.commit()
    return apps_repo.get(conn, current.id)
