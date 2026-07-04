"""Environment self-check — the first command to run on any new machine.

    uv run python -m waloader.tools.doctor            # full check (needs network)
    uv run python -m waloader.tools.doctor --offline  # skip the uv preflight

Exit code 0 = every check passed (skips don't fail).
"""

from __future__ import annotations

import argparse
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from waloader import db
from waloader.config import ConfigError, apply_db_overrides, load_config
from waloader.paths import ensure_dir
from waloader.repositories import settings as settings_repo
from waloader.services import ports, preflight


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str


def _version_of(binary: str, *args: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, no shell
            [binary, *args], capture_output=True, text=True, timeout=20
        )
        return (completed.stdout or completed.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError) as exc:
        return f"error: {exc}"


def run_checks(*, offline: bool = False) -> list[Check]:
    checks: list[Check] = []

    checks.append(Check("platform", "PASS",
                        f"{platform.platform()} · Python {platform.python_version()}"))

    try:
        loaded = load_config()
        config = loaded.config
        checks.append(Check("config", "PASS",
                            str(loaded.config_path or "built-in defaults")))
    except ConfigError as exc:
        checks.append(Check("config", "FAIL", str(exc)))
        return checks

    python_binary = config.resolved_python()
    if Path(python_binary).exists():
        checks.append(Check("python binary", "PASS",
                            f"{python_binary} · {_version_of(python_binary, '--version')}"))
    else:
        checks.append(Check("python binary", "FAIL", f"not found: {python_binary}"))

    uv_binary = config.resolved_uv()
    if uv_binary:
        checks.append(Check("uv binary", "PASS",
                            f"{uv_binary} · {_version_of(uv_binary, '--version')}"))
    else:
        checks.append(Check("uv binary", "FAIL",
                            "uv not found (set executables.uv_binary or add to PATH)"))

    try:
        ensure_dir(config.data_dir)
        probe = config.data_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("data dir writable", "PASS", str(config.data_dir)))
    except OSError as exc:
        checks.append(Check("data dir writable", "FAIL", str(exc)))
        return checks

    try:
        conn = db.connect(config.database_path)
        try:
            pending = [s for s in db.migration_status(conn) if not s.applied]
            if pending and not config.database.auto_migrate:
                checks.append(Check("database", "FAIL",
                                    f"{len(pending)} pending migration(s), auto_migrate off"))
            else:
                if pending:
                    db.migrate(conn)
                checks.append(Check("database", "PASS", str(config.database_path)))
            config = apply_db_overrides(loaded, settings_repo.get_all(conn)).config
        finally:
            conn.close()
    except Exception as exc:
        checks.append(Check("database", "FAIL", str(exc)))

    free = next(
        (p for p in range(config.ports.child_app_start, config.ports.child_app_end + 1)
         if ports.port_is_free(p)),
        None,
    )
    if free is not None:
        checks.append(Check("child port range", "PASS",
                            f"{config.ports.child_app_start}-{config.ports.child_app_end} "
                            f"(e.g. {free} free)"))
    else:
        checks.append(Check("child port range", "FAIL", "no free port in range"))

    if config.caddy.enabled:
        caddy_binary = config.resolved_caddy()
        if caddy_binary:
            checks.append(Check("caddy binary", "PASS",
                                f"{caddy_binary} · {_version_of(caddy_binary, 'version')}"))
        else:
            checks.append(Check("caddy binary", "FAIL",
                                "caddy.enabled=true but no caddy binary found"))
    else:
        found = config.resolved_caddy()
        note = f"found: {found}" if found else "not found"
        checks.append(Check("caddy binary", "SKIP", f"caddy disabled (binary {note})"))

    if offline:
        checks.append(Check("uv preflight", "SKIP", "--offline"))
    elif uv_binary:
        result = preflight.run_preflight(config)
        if result.ok:
            checks.append(Check("uv preflight", "PASS", result.command_display))
        else:
            checks.append(Check("uv preflight", "FAIL",
                                f"{result.command_display}\n{result.output}"))
    else:
        checks.append(Check("uv preflight", "SKIP", "no uv binary"))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.doctor", description="WALoader environment self-check"
    )
    parser.add_argument("--offline", action="store_true",
                        help="skip checks that need network access (uv preflight)")
    args = parser.parse_args(argv)

    checks = run_checks(offline=args.offline)
    failed = False
    for check in checks:
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[check.status]
        print(f"[{mark}] {check.name:18} {check.detail}")
        failed = failed or check.status == "FAIL"
    print()
    print("doctor: FAILED — fix the ✗ items above" if failed else "doctor: all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
