"""uv command/environment builder with credential redaction.

Single seam for every uv invocation (preflight, venv creation, installs) so
UV_CONFIG_FILE / UV_CACHE_DIR / UV_SYSTEM_CERTS / SSL_* handling and secret
redaction behave identically everywhere. The CONTENTS of uv.config_file are
never read, printed, or logged — only the path is exported.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from waloader.config import WALoaderConfig

_URL_CREDS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+@")
_TOKEN_PARAM_RE = re.compile(r"(?i)([?&](?:token|access_token|api_key|apikey|password)=)[^&\s]+")


class UvNotFoundError(Exception):
    pass


def redact(text: str) -> str:
    """Strip credentials from URLs and token query params in surfaced text."""
    text = _URL_CREDS_RE.sub(r"\g<scheme>***@", text)
    return _TOKEN_PARAM_RE.sub(r"\1***", text)


def build_env(
    config: WALoaderConfig, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Copy of the environment with uv settings applied; nothing else touched."""
    env = dict(os.environ if base_env is None else base_env)
    if config.uv.config_file:
        env["UV_CONFIG_FILE"] = config.uv.config_file
    env["UV_CACHE_DIR"] = str(config.uv_cache_dir)
    if config.uv.system_certs:
        env["UV_SYSTEM_CERTS"] = "true"
    if config.uv.ssl_cert_file:
        env["SSL_CERT_FILE"] = config.uv.ssl_cert_file
    if config.uv.ssl_cert_dir:
        env["SSL_CERT_DIR"] = config.uv.ssl_cert_dir
    return env


def uv_command(
    config: WALoaderConfig, *args: str, python: str | Path | None = None
) -> list[str]:
    uv_binary = config.resolved_uv()
    if uv_binary is None:
        raise UvNotFoundError(
            "uv executable not found. Set executables.uv_binary in the config "
            "or put 'uv' on PATH."
        )
    command = [uv_binary, *args]
    if python is not None:
        command += ["--python", str(python)]
    for host in config.uv.allow_insecure_hosts:
        command += ["--allow-insecure-host", host]
    return command


def venv_python(venv_path: Path) -> Path:
    """Path to the interpreter inside a venv, cross-platform."""
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def describe_command(command: list[str], env: dict[str, str]) -> str:
    """One redacted, copyable line for logs/error blocks (env names, not values)."""
    exported = [
        name
        for name in ("UV_CONFIG_FILE", "UV_CACHE_DIR", "UV_SYSTEM_CERTS",
                     "SSL_CERT_FILE", "SSL_CERT_DIR")
        if name in env
    ]
    prefix = f"[env: {', '.join(exported)}] " if exported else ""
    return prefix + redact(" ".join(command))
