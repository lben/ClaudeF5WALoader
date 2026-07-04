"""Database CLI: migrate | status | backup."""

from __future__ import annotations

import argparse
import sys

from waloader import db
from waloader.config import ConfigError, load_config
from waloader.services import backups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.db", description="WALoader database maintenance"
    )
    parser.add_argument("command", choices=["migrate", "status", "backup"])
    args = parser.parse_args(argv)

    try:
        config = load_config().config
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    conn = db.connect(config.database_path)
    try:
        if args.command == "migrate":
            applied = db.migrate(conn)
            if applied:
                for migration in applied:
                    print(f"applied {migration.version:03d}_{migration.name}")
            else:
                print("database is up to date")
        elif args.command == "status":
            print(f"database: {config.database_path}")
            for status in db.migration_status(conn):
                mark = "applied" if status.applied else "PENDING"
                stamp = f" at {status.applied_at}" if status.applied_at else ""
                print(f"  {status.version:03d}_{status.name}: {mark}{stamp}")
        elif args.command == "backup":
            result = backups.backup_database(config)
            print(result.reason + (f": {result.path}" if result.path else ""))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
