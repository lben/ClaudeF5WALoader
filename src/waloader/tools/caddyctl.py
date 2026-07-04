"""Caddy CLI: generate | validate | start | stop | reload | status."""

from __future__ import annotations

import argparse

from waloader.services import caddy
from waloader.tools._common import bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.caddyctl", description="Manage the Caddy reverse proxy"
    )
    parser.add_argument(
        "command", choices=["generate", "validate", "start", "stop", "reload", "status"]
    )
    args = parser.parse_args(argv)

    config, conn = bootstrap()
    try:
        if args.command == "generate":
            path = caddy.write_caddyfile(conn, config)
            print(f"caddyfile written: {path}")
            return 0
        if args.command == "validate":
            result = caddy.validate(config)
        elif args.command == "start":
            result = caddy.start(conn, config)
        elif args.command == "stop":
            result = caddy.stop(config)
        elif args.command == "reload":
            result = caddy.reload(conn, config)
        else:  # status
            info = caddy.status(conn, config)
            for key, value in info.items():
                print(f"{key:14} {value}")
            return 0
        print(result.output or ("ok" if result.ok else "failed"))
        return 0 if result.ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
