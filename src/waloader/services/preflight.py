"""uv preflight: prove package-index connectivity before any deployment.

Resolves (never installs) the configured preflight packages into a dedicated
throwaway venv using exactly the same uv binary and environment deployments
use. Output is credential-redacted and copyable.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from waloader.config import WALoaderConfig
from waloader.paths import ensure_dir
from waloader.services import uv_env

log = logging.getLogger(__name__)

PREFLIGHT_TIMEOUT_SECONDS = 180


@dataclass
class PreflightResult:
    ok: bool
    command_display: str
    output: str
    returncode: int | None = None


def _run(command: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - argv list, no shell
        command, env=env, capture_output=True, text=True, timeout=timeout
    )


def run_preflight(config: WALoaderConfig, *, runner=_run) -> PreflightResult:
    env = uv_env.build_env(config)
    try:
        venv_path = config.tmp_dir / "preflight-venv"
        python_in_venv = uv_env.venv_python(venv_path)
        if not python_in_venv.exists():
            ensure_dir(config.tmp_dir)
            create = uv_env.uv_command(
                config, "venv", str(venv_path), python=config.resolved_python()
            )
            log.info("preflight: creating venv: %s", uv_env.describe_command(create, env))
            created = runner(create, env, PREFLIGHT_TIMEOUT_SECONDS)
            if created.returncode != 0:
                return PreflightResult(
                    ok=False,
                    command_display=uv_env.describe_command(create, env),
                    output=uv_env.redact(created.stdout + "\n" + created.stderr).strip(),
                    returncode=created.returncode,
                )

        command = uv_env.uv_command(
            config,
            "pip",
            "install",
            "--dry-run",
            *config.uv.preflight_packages,
            python=python_in_venv,
        )
        display = uv_env.describe_command(command, env)
        log.info("preflight: %s", display)
        completed = runner(command, env, PREFLIGHT_TIMEOUT_SECONDS)
        return PreflightResult(
            ok=completed.returncode == 0,
            command_display=display,
            output=uv_env.redact(completed.stdout + "\n" + completed.stderr).strip(),
            returncode=completed.returncode,
        )
    except uv_env.UvNotFoundError as exc:
        return PreflightResult(ok=False, command_display="uv", output=str(exc))
    except subprocess.TimeoutExpired:
        return PreflightResult(
            ok=False,
            command_display="uv pip install --dry-run",
            output=f"Preflight timed out after {PREFLIGHT_TIMEOUT_SECONDS}s "
            "(package index unreachable?)",
        )
