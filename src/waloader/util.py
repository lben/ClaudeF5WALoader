"""Small shared helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """UTC ISO 8601 with seconds precision — the storage format for all timestamps."""
    return utc_now().replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
