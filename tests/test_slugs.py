from __future__ import annotations

import sqlite3

import pytest

from waloader.models import User
from waloader.repositories import apps as apps_repo
from waloader.services import slugs


class TestSlugify:
    def test_canonical_examples(self) -> None:
        assert slugs.slugify("Client Positions Dashboard") == "client-positions-dashboard"
        assert slugs.slugify("P&L Explain 2026!") == "p-l-explain-2026"

    def test_rules(self) -> None:
        assert slugs.slugify("  Hello   World  ") == "hello-world"
        assert slugs.slugify("a--b---c") == "a-b-c"
        assert slugs.slugify("Café Réports") == "cafe-reports"  # accents fold to ascii
        assert slugs.slugify("UPPER") == "upper"

    def test_max_length_no_trailing_hyphen(self) -> None:
        name = "word " * 30
        slug = slugs.slugify(name)
        assert len(slug) <= slugs.MAX_SLUG_LENGTH
        assert not slug.endswith("-")

    def test_empty_rejected(self) -> None:
        with pytest.raises(slugs.SlugError):
            slugs.slugify("!!! ***")

    def test_reserved(self) -> None:
        for name in ("waloader", "Admin", "APPS", "app-link", "private", "caddy"):
            assert slugs.is_reserved(slugs.slugify(name))
        assert not slugs.is_reserved("client-positions")


class TestAvailability:
    def test_available(self, conn: sqlite3.Connection) -> None:
        result = slugs.check_name_available(conn, "Fresh App")
        assert result.available and result.slug == "fresh-app"

    def test_reserved_name(self, conn: sqlite3.Connection) -> None:
        result = slugs.check_name_available(conn, "Admin")
        assert not result.available and "reserved" in result.reason

    def test_name_taken(self, conn: sqlite3.Connection, user: User) -> None:
        apps_repo.create(conn, owner_id=user.id, name="Taken App", slug="taken-app")
        conn.commit()
        result = slugs.check_name_available(conn, "taken app")
        assert not result.available and "already taken" in result.reason

    def test_slug_collision_from_different_name(
        self, conn: sqlite3.Connection, user: User
    ) -> None:
        apps_repo.create(conn, owner_id=user.id, name="P&L Explain", slug="p-l-explain")
        conn.commit()
        result = slugs.check_name_available(conn, "P L Explain")  # same slug, other name
        assert not result.available and "URL name" in result.reason

    def test_exclude_self_when_editing(self, conn: sqlite3.Connection, user: User) -> None:
        app = apps_repo.create(conn, owner_id=user.id, name="Mine", slug="mine")
        conn.commit()
        assert slugs.check_name_available(conn, "Mine", exclude_app_id=app.id).available

    def test_unusable_name(self, conn: sqlite3.Connection) -> None:
        result = slugs.check_name_available(conn, "!!!")
        assert not result.available and result.slug == ""
