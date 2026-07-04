"""Shared CLI bootstrap: config + DB + logging, identical to what serve uses."""

from __future__ import annotations

import sqlite3
import sys

from waloader import db
from waloader.config import ConfigError, WALoaderConfig, apply_db_overrides, load_config
from waloader.logging_setup import setup_logging
from waloader.repositories import settings as settings_repo


class BootstrapError(SystemExit):
    pass


def bootstrap(*, console_logging: bool = False) -> tuple[WALoaderConfig, sqlite3.Connection]:
    """Load config (TOML + DB overrides), open the DB, apply migrations."""
    try:
        loaded = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    conn = db.connect(loaded.config.database_path)
    if loaded.config.database.auto_migrate:
        db.migrate(conn)
    else:
        pending = [s for s in db.migration_status(conn) if not s.applied]
        if pending:
            print(
                "Database has pending migrations and database.auto_migrate is off. "
                "Run: uv run python -m waloader.tools.db migrate",
                file=sys.stderr,
            )
            raise SystemExit(2)

    loaded = apply_db_overrides(loaded, settings_repo.get_all(conn))
    setup_logging(loaded.config, console=console_logging)
    return loaded.config, conn


def fail(message: str, code: int = 1) -> SystemExit:
    print(message, file=sys.stderr)
    return SystemExit(code)
