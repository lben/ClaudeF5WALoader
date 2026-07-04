"""Background worker: periodic health checks + daily maintenance, no cron.

Runs as a daemon thread inside the WALoader UI process (started once via
st.cache_resource). Limitation, by design and documented: checks only run
while WALoader itself runs. Every job is also operator-triggerable from the
admin panel and the maintenance CLI.
"""

from __future__ import annotations

import logging
import threading

from waloader import db
from waloader.config import WALoaderConfig, apply_db_overrides, load_config
from waloader.repositories import settings as settings_repo
from waloader.services import health, maintenance_service
from waloader.util import utc_now

log = logging.getLogger(__name__)


def _fresh_config() -> WALoaderConfig:
    loaded = load_config()
    conn = db.connect(loaded.config.database_path)
    try:
        overrides = settings_repo.get_all(conn)
    finally:
        conn.close()
    return apply_db_overrides(loaded, overrides).config


class BackgroundWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_maintenance_date: str | None = None
        self.ticks = 0
        self.maintenance_runs = 0

    def tick(self) -> None:
        """One pass: health-check running apps; run maintenance once per day."""
        config = _fresh_config()
        conn = db.connect(config.database_path)
        try:
            outcomes = health.check_all_running(conn, config)
            for outcome in outcomes:
                if outcome.marked_failed:
                    log.warning("health: %s failed (%s)%s", outcome.slug,
                                outcome.reason,
                                " — crash email sent" if outcome.email_sent else "")
            today = utc_now().date().isoformat()
            if self._last_maintenance_date != today:
                report = maintenance_service.run_all(conn, config)
                self._last_maintenance_date = today
                self.maintenance_runs += 1
                log.info("daily maintenance: %s", report.summary())
        finally:
            conn.close()
        self.ticks += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("background worker tick failed; continuing")
            try:
                interval = _fresh_config().health.interval_seconds
            except Exception:
                interval = 30
            self._stop.wait(max(1, interval))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="waloader-background", daemon=True
        )
        self._thread.start()
        log.info("background worker started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)


_worker: BackgroundWorker | None = None
_worker_lock = threading.Lock()


def start_background_worker() -> BackgroundWorker:
    """Process-wide singleton start (safe to call from every rerun)."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = BackgroundWorker()
        _worker.start()
        return _worker
