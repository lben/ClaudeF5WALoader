"""Central logging configuration.

Log layout (under the configured logs dir):
    waloader/app.log    — everything at INFO+
    waloader/error.log  — ERROR+ only
Per-app deploy/runtime/test logs are written by the deployment/process services.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from waloader.config import WALoaderConfig
from waloader.paths import ensure_dir

_CONFIGURED = False

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(config: WALoaderConfig, *, console: bool = True) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    log_dir = ensure_dir(config.logs_dir / "waloader")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(FORMAT)

    app_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)
    root.addHandler(app_handler)

    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "error.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if config.debug.rich_tracebacks_enabled:
        try:
            from rich.traceback import install

            install(show_locals=config.debug.rich_tracebacks_show_locals)
        except Exception:  # rich is a hard dep, but never let cosmetics break startup
            logging.getLogger(__name__).debug("rich traceback install failed", exc_info=True)

    # Quiet down noisy third-party loggers in the shared files.
    for noisy in ("watchdog", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def app_log_dir(config: WALoaderConfig, slug: str, version: int | None = None) -> Path:
    base = config.logs_dir / "apps" / slug
    if version is not None:
        base = base / f"{version:06d}"
    return ensure_dir(base)
