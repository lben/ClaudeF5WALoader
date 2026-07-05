"""Configuration system.

Precedence (lowest to highest): built-in defaults -> TOML file -> DB-stored
settings (admin panel edits, applied via ``apply_db_overrides``).

The TOML file is found via: explicit argument -> $WALOADER_CONFIG ->
``config/waloader.toml`` relative to the working directory. Bootstrap settings
(``[paths]``) can only come from defaults/TOML since the DB is derived from them.

Every field defined here must be documented in ``config/waloader.example.toml``
and ``docs/configuration.md``. ``extra="forbid"`` makes undocumented TOML keys a
hard error.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

DEFAULT_CONFIG_LOCATION = Path("config") / "waloader.toml"

# Dotted-path prefixes the admin panel may override via the DB settings table.
# [paths] is bootstrap-only: the DB itself lives under paths.data_dir.
DB_EDITABLE_PREFIXES = (
    "server.",
    "executables.",
    "uv.",
    "ports.",
    "caddy.",
    "dependencies_policy.",
    "uploads.",
    "health.",
    "notifications.",
    "retention.",
    "database.",
    "debug.",
    "apps.",
)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerConfig(_Section):
    public_host: str = "localhost"


class PathsConfig(_Section):
    data_dir: str = "data"
    # Empty string means "derive from data_dir".
    database_path: str = ""
    apps_dir: str = ""
    logs_dir: str = ""
    backups_dir: str = ""
    archives_dir: str = ""
    uploads_dir: str = ""
    tmp_dir: str = ""
    uv_cache_dir: str = ""
    caddy_config_path: str = ""
    caddy_logs_dir: str = ""


class ExecutablesConfig(_Section):
    python_binary: str = ""
    uv_binary: str = "uv"
    caddy_binary: str = ""


class UvConfig(_Section):
    config_file: str = ""  # path only; contents are secret and must never be read
    cache_dir: str = ""  # empty -> derived data/uv-cache
    system_certs: bool = False
    ssl_cert_file: str = ""
    ssl_cert_dir: str = ""
    allow_insecure_hosts: list[str] = []
    preflight_packages: list[str] = ["pandas"]


class PortsConfig(_Section):
    waloader_port: int = 8501
    child_app_start: int = 8601
    child_app_end: int = 8999
    caddy_public_port: int = 8080

    @field_validator("child_app_end")
    @classmethod
    def _range_ok(cls, v: int, info: Any) -> int:
        start = info.data.get("child_app_start")
        if start is not None and v < start:
            raise ValueError("ports.child_app_end must be >= ports.child_app_start")
        return v


class CaddyConfig(_Section):
    enabled: bool = False
    admin_port: int = 2019


class DependenciesPolicyConfig(_Section):
    allow_app_dependencies: bool = True
    allow_direct_url_dependencies: bool = False
    allow_vcs_dependencies: bool = False
    allow_path_dependencies: bool = False
    require_admin_approval_for_new_dependencies: bool = False


class UploadsConfig(_Section):
    max_markdown_bundle_mb: int = 10
    max_dataset_file_mb: int = 250
    max_bundle_files: int = 200
    allowed_dataset_extensions: list[str] = [".csv", ".xlsx", ".xls", ".parquet"]
    default_excel_sheet_name: str = "Sheet1"


class HealthConfig(_Section):
    interval_seconds: int = 30
    http_timeout_seconds: int = 5
    grace_period_seconds: int = 180
    consecutive_failures_threshold: int = 3
    initial_check_timeout_seconds: int = 30
    background_enabled: bool = True  # health+maintenance thread inside the UI process


class NotificationsConfig(_Section):
    crash_emails_enabled: bool = True
    sender: str = "waloader@localhost"
    admin_cc: list[str] = []


class RetentionConfig(_Section):
    backup_days: int = 183
    log_days: int = 183
    deleted_app_days: int = 183
    factory_reset_backup_days: int = 183  # ~6 months; factory-reset safety backups


class DatabaseConfig(_Section):
    auto_migrate: bool = True


class DebugConfig(_Section):
    rich_tracebacks_enabled: bool = True
    rich_tracebacks_show_locals: bool = False


class AppsConfig(_Section):
    bind_address: str = "auto"  # auto -> 127.0.0.1 behind Caddy, 0.0.0.0 direct mode
    stop_timeout_seconds: int = 10
    test_timeout_seconds: int = 300
    base_dependencies: list[str] = ["streamlit", "pandas", "plotly", "duckdb", "pyarrow"]
    sdk_dependencies: list[str] = ["argon2-cffi"]  # always installed; waloader_sdk.auth needs it


class WALoaderConfig(_Section):
    server: ServerConfig = ServerConfig()
    paths: PathsConfig = PathsConfig()
    executables: ExecutablesConfig = ExecutablesConfig()
    uv: UvConfig = UvConfig()
    ports: PortsConfig = PortsConfig()
    caddy: CaddyConfig = CaddyConfig()
    dependencies_policy: DependenciesPolicyConfig = DependenciesPolicyConfig()
    uploads: UploadsConfig = UploadsConfig()
    health: HealthConfig = HealthConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    retention: RetentionConfig = RetentionConfig()
    database: DatabaseConfig = DatabaseConfig()
    debug: DebugConfig = DebugConfig()
    apps: AppsConfig = AppsConfig()

    # --- derived paths -------------------------------------------------
    def _derived(self, override: str, *parts: str) -> Path:
        if override:
            return Path(override).expanduser().resolve()
        return self.data_dir.joinpath(*parts)

    @property
    def data_dir(self) -> Path:
        return Path(self.paths.data_dir).expanduser().resolve()

    @property
    def database_path(self) -> Path:
        return self._derived(self.paths.database_path, "waloader.db")

    @property
    def apps_dir(self) -> Path:
        return self._derived(self.paths.apps_dir, "apps")

    @property
    def logs_dir(self) -> Path:
        return self._derived(self.paths.logs_dir, "logs")

    @property
    def backups_dir(self) -> Path:
        return self._derived(self.paths.backups_dir, "backups")

    @property
    def archives_dir(self) -> Path:
        return self._derived(self.paths.archives_dir, "archives")

    @property
    def uploads_dir(self) -> Path:
        return self._derived(self.paths.uploads_dir, "uploads")

    @property
    def tmp_dir(self) -> Path:
        return self._derived(self.paths.tmp_dir, "tmp")

    @property
    def uv_cache_dir(self) -> Path:
        override = self.paths.uv_cache_dir or self.uv.cache_dir
        return self._derived(override, "uv-cache")

    @property
    def caddy_config_path(self) -> Path:
        return self._derived(self.paths.caddy_config_path, "caddy", "Caddyfile")

    @property
    def caddy_logs_dir(self) -> Path:
        return self._derived(self.paths.caddy_logs_dir, "logs", "caddy")

    def derived_paths(self) -> dict[str, Path]:
        return {
            "data_dir": self.data_dir,
            "database_path": self.database_path,
            "apps_dir": self.apps_dir,
            "logs_dir": self.logs_dir,
            "backups_dir": self.backups_dir,
            "archives_dir": self.archives_dir,
            "uploads_dir": self.uploads_dir,
            "tmp_dir": self.tmp_dir,
            "uv_cache_dir": self.uv_cache_dir,
            "caddy_config_path": self.caddy_config_path,
            "caddy_logs_dir": self.caddy_logs_dir,
        }

    # --- executable resolution -----------------------------------------
    def resolved_python(self) -> str:
        if self.executables.python_binary:
            return self.executables.python_binary
        found = shutil.which("python3.12")
        return found or sys.executable

    def resolved_uv(self) -> str | None:
        binary = self.executables.uv_binary or "uv"
        if Path(binary).name != binary:  # explicit path given
            return binary if Path(binary).exists() else None
        return shutil.which(binary)

    def resolved_caddy(self) -> str | None:
        binary = self.executables.caddy_binary
        if binary:
            if Path(binary).name != binary:
                return binary if Path(binary).exists() else None
            return shutil.which(binary)
        return shutil.which("caddy")


@dataclass
class LoadedConfig:
    """A config plus where each dotted key came from (default/toml/db)."""

    config: WALoaderConfig
    sources: dict[str, str] = field(default_factory=dict)
    config_path: Path | None = None

    def source_of(self, dotted: str) -> str:
        return self.sources.get(dotted, "default")


class ConfigError(Exception):
    pass


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _find_config_file(explicit: str | Path | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        return path
    env = os.environ.get("WALOADER_CONFIG", "")
    if env:
        path = Path(env)
        if not path.exists():
            raise ConfigError(f"$WALOADER_CONFIG points to a missing file: {path}")
        return path
    if DEFAULT_CONFIG_LOCATION.exists():
        return DEFAULT_CONFIG_LOCATION
    return None


def load_config(path: str | Path | None = None) -> LoadedConfig:
    config_path = _find_config_file(path)
    data: dict[str, Any] = {}
    if config_path is not None:
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    try:
        config = WALoaderConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration ({config_path or 'defaults'}): {exc}") from exc
    sources = {key: "toml" for key in _flatten(data)}
    return LoadedConfig(config=config, sources=sources, config_path=config_path)


def is_db_editable(dotted: str) -> bool:
    return dotted.startswith(DB_EDITABLE_PREFIXES)


def apply_db_overrides(loaded: LoadedConfig, overrides: dict[str, Any]) -> LoadedConfig:
    """Overlay DB settings (dotted key -> JSON-decoded value) onto a config."""
    if not overrides:
        return loaded
    data = loaded.config.model_dump()
    sources = dict(loaded.sources)
    for dotted, value in overrides.items():
        if not is_db_editable(dotted):
            continue  # paths.* and unknown prefixes are bootstrap/TOML-only
        section, _, key = dotted.partition(".")
        if section not in data or key not in data[section]:
            continue  # stale key from an older version; ignore rather than crash
        data[section][key] = value
        sources[dotted] = "db"
    try:
        config = WALoaderConfig.model_validate(data)
    except Exception as exc:
        raise ConfigError(f"Invalid DB settings override: {exc}") from exc
    return LoadedConfig(config=config, sources=sources, config_path=loaded.config_path)


def decode_setting(value_json: str) -> Any:
    return json.loads(value_json)


def encode_setting(value: Any) -> str:
    return json.dumps(value)
