"""Runtime context for SDK calls, resolved from WALOADER_* environment
variables injected by the WALoader process manager at launch."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class WALoaderEnvError(RuntimeError):
    """Raised when SDK code runs outside a WALoader-managed app."""


@dataclass(frozen=True)
class SdkContext:
    app_slug: str
    app_name: str
    db_path: Path
    data_dir: Path
    datasets_dir: Path

    def resolve(self, stored_relative: str) -> Path:
        """DB-stored data_dir-relative POSIX path -> absolute path."""
        return self.data_dir.joinpath(*PurePosixPath(stored_relative).parts)


def get_context(app_slug: str | None = None) -> SdkContext:
    slug = app_slug or os.environ.get("WALOADER_APP_SLUG", "")
    db_path = os.environ.get("WALOADER_DB_PATH", "")
    data_dir = os.environ.get("WALOADER_DATA_DIR", "")
    if not slug or not db_path or not data_dir:
        raise WALoaderEnvError(
            "waloader_sdk needs the WALOADER_APP_SLUG / WALOADER_DB_PATH / "
            "WALOADER_DATA_DIR environment variables, which WALoader injects when "
            "it launches your app. Running outside WALoader? Guard the import or "
            "provide a local fallback (see docs/llm-bundle-prompt.md)."
        )
    return SdkContext(
        app_slug=slug,
        app_name=os.environ.get("WALOADER_APP_NAME", slug),
        db_path=Path(db_path),
        data_dir=Path(data_dir),
        datasets_dir=Path(os.environ.get("WALOADER_DATASETS_DIR", "")),
    )


def connect(context: SdkContext) -> sqlite3.Connection:
    conn = sqlite3.connect(context.db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
