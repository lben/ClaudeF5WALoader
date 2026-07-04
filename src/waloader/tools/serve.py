"""Launch the WALoader UI with the correct flags derived from config.

    uv run python -m waloader.tools.serve

Runs migrations, reconciles state, then execs Streamlit in the foreground
(Ctrl+C stops WALoader; child apps keep running detached).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import waloader
from waloader.config import WALoaderConfig
from waloader.services import reconciliation
from waloader.tools._common import bootstrap

UI_ENTRYPOINT = Path(waloader.__file__).parent / "ui" / "app.py"


def build_serve_command(config: WALoaderConfig) -> list[str]:
    command = [
        sys.executable, "-m", "streamlit", "run", str(UI_ENTRYPOINT),
        "--server.port", str(config.ports.waloader_port),
        "--server.address", "127.0.0.1" if config.caddy.enabled else "0.0.0.0",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.maxUploadSize", str(
            max(config.uploads.max_markdown_bundle_mb, config.uploads.max_dataset_file_mb)
        ),
    ]
    if config.caddy.enabled:
        command += ["--server.baseUrlPath", "waloader"]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.serve", description="Run the WALoader UI"
    )
    parser.add_argument("--print-command", action="store_true",
                        help="show the streamlit command without launching")
    args = parser.parse_args(argv)

    config, conn = bootstrap(console_logging=True)
    try:
        report = reconciliation.reconcile(conn, config)
        if report.resume_candidates:
            print(
                "reconcile: previously-running apps found stopped: "
                + ", ".join(report.resume_candidates)
                + "  (resume from the admin panel or appctl start)"
            )
    finally:
        conn.close()

    command = build_serve_command(config)
    if config.caddy.enabled:
        url = f"http://{config.server.public_host}:{config.ports.caddy_public_port}/waloader"
    else:
        url = f"http://{config.server.public_host}:{config.ports.waloader_port}"
    print(f"WALoader UI: {url}")
    if args.print_command:
        print(" ".join(command))
        return 0
    return subprocess.call(command)  # noqa: S603 - argv list, no shell


if __name__ == "__main__":
    raise SystemExit(main())
