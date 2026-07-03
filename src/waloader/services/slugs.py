"""App slug generation, reserved names, and availability checks."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from waloader.repositories import apps as apps_repo

MAX_SLUG_LENGTH = 60

RESERVED_SLUGS = frozenset(
    {
        "waloader", "admin", "login", "logout", "api", "static", "assets",
        "private", "health", "apps", "app-link", "caddy",
    }
)


class SlugError(ValueError):
    pass


def slugify(name: str) -> str:
    """'Client Positions Dashboard' -> 'client-positions-dashboard'."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].rstrip("-")
    if not slug:
        raise SlugError("App name must contain at least one letter or number")
    return slug


def is_reserved(slug: str) -> bool:
    return slug.lower() in RESERVED_SLUGS


@dataclass(frozen=True)
class Availability:
    available: bool
    slug: str
    reason: str = ""


def check_name_available(
    conn: sqlite3.Connection, name: str, *, exclude_app_id: int | None = None
) -> Availability:
    """Live availability check used while the user types an app name."""
    try:
        slug = slugify(name)
    except SlugError as exc:
        return Availability(False, "", str(exc))
    if is_reserved(slug):
        return Availability(False, slug, f"'{slug}' is a reserved name")
    if apps_repo.name_taken(conn, name.strip(), exclude_app_id=exclude_app_id):
        return Availability(False, slug, "This app name is already taken")
    if apps_repo.slug_taken(conn, slug, exclude_app_id=exclude_app_id):
        return Availability(False, slug, f"The URL name '{slug}' is already taken")
    return Availability(True, slug)
