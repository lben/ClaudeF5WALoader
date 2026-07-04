"""SQLite backups: consistent snapshots, change detection, retention."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from waloader.config import WALoaderConfig
from waloader.paths import ensure_dir
from waloader.util import utc_now

log = logging.getLogger(__name__)


@dataclass
class BackupResult:
    created: bool
    path: Path | None
    reason: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_backup_hash(backups_dir: Path) -> str | None:
    sidecars = sorted(backups_dir.glob("waloader-*.db.sha256"))
    if not sidecars:
        return None
    return sidecars[-1].read_text(encoding="utf-8").strip()


def backup_database(config: WALoaderConfig) -> BackupResult:
    """Snapshot via the sqlite backup API; keep only if the content changed."""
    db_path = config.database_path
    if not db_path.exists():
        return BackupResult(False, None, f"database does not exist yet: {db_path}")
    backups_dir = ensure_dir(config.backups_dir)

    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    target = backups_dir / f"waloader-{stamp}.db"
    counter = 1
    while target.exists():
        target = backups_dir / f"waloader-{stamp}-{counter}.db"
        counter += 1
    temp = target.with_suffix(".db.tmp")

    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(temp)
        try:
            source.backup(dest)  # consistent even with concurrent writers (WAL)
        finally:
            dest.close()
    finally:
        source.close()

    snapshot_hash = _sha256(temp)
    if snapshot_hash == _latest_backup_hash(backups_dir):
        temp.unlink()
        return BackupResult(False, None, "database unchanged since last backup")

    temp.rename(target)
    target.with_name(target.name + ".sha256").write_text(snapshot_hash, encoding="utf-8")
    log.info("database backup written: %s", target)
    return BackupResult(True, target, "backup created")


def cleanup_backups(config: WALoaderConfig) -> list[Path]:
    """Delete backups (and sidecars) older than retention.backup_days."""
    cutoff = time.time() - config.retention.backup_days * 86400
    removed = []
    if not config.backups_dir.exists():
        return removed
    for path in sorted(config.backups_dir.glob("waloader-*")):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
    if removed:
        log.info("removed %d expired backup file(s)", len(removed))
    return removed


def cleanup_logs(config: WALoaderConfig) -> int:
    """Delete log files older than retention.log_days; prune empty dirs."""
    cutoff = time.time() - config.retention.log_days * 86400
    removed = 0
    logs_dir = config.logs_dir
    if not logs_dir.exists():
        return 0
    for path in sorted(logs_dir.rglob("*"), reverse=True):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:  # a live handle (open log) — skip, next run gets it
            continue
    if removed:
        log.info("removed %d expired log file(s)", removed)
    return removed
