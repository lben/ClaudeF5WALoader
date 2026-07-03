from __future__ import annotations

from pathlib import Path

import pytest

from waloader.config import (
    ConfigError,
    LoadedConfig,
    WALoaderConfig,
    apply_db_overrides,
    is_db_editable,
    load_config,
)


def test_defaults() -> None:
    config = WALoaderConfig()
    assert config.ports.waloader_port == 8501
    assert config.ports.child_app_start == 8601
    assert config.ports.child_app_end == 8999
    assert config.ports.caddy_public_port == 8080
    assert config.uploads.max_markdown_bundle_mb == 10
    assert config.uploads.max_dataset_file_mb == 250
    assert config.uploads.default_excel_sheet_name == "Sheet1"
    assert config.retention.backup_days == 183
    assert config.retention.log_days == 183
    assert config.retention.deleted_app_days == 183
    assert config.dependencies_policy.allow_app_dependencies is True
    assert config.dependencies_policy.allow_vcs_dependencies is False
    assert config.debug.rich_tracebacks_enabled is True
    assert config.debug.rich_tracebacks_show_locals is False
    assert config.uv.preflight_packages == ["pandas"]
    assert not config.caddy.enabled


def test_derived_paths_follow_data_dir(tmp_path: Path) -> None:
    config = WALoaderConfig.model_validate({"paths": {"data_dir": str(tmp_path / "d")}})
    base = (tmp_path / "d").resolve()
    assert config.database_path == base / "waloader.db"
    assert config.apps_dir == base / "apps"
    assert config.logs_dir == base / "logs"
    assert config.backups_dir == base / "backups"
    assert config.archives_dir == base / "archives"
    assert config.uploads_dir == base / "uploads"
    assert config.tmp_dir == base / "tmp"
    assert config.uv_cache_dir == base / "uv-cache"
    assert config.caddy_config_path == base / "caddy" / "Caddyfile"
    assert config.caddy_logs_dir == base / "logs" / "caddy"


def test_explicit_path_overrides_derivation(tmp_path: Path) -> None:
    config = WALoaderConfig.model_validate(
        {
            "paths": {
                "data_dir": str(tmp_path / "d"),
                "database_path": str(tmp_path / "elsewhere" / "wal.db"),
            },
            "uv": {"cache_dir": str(tmp_path / "uvcache")},
        }
    )
    assert config.database_path == (tmp_path / "elsewhere" / "wal.db").resolve()
    assert config.uv_cache_dir == (tmp_path / "uvcache").resolve()


def test_load_from_toml_and_sources(tmp_path: Path) -> None:
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        '[server]\npublic_host = "finbox01"\n[ports]\nwaloader_port = 9000\n',
        encoding="utf-8",
    )
    loaded = load_config(toml)
    assert loaded.config.server.public_host == "finbox01"
    assert loaded.config.ports.waloader_port == 9000
    assert loaded.source_of("server.public_host") == "toml"
    assert loaded.source_of("ports.child_app_start") == "default"


def test_env_var_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "custom.toml"
    toml.write_text("[ports]\nwaloader_port = 9100\n", encoding="utf-8")
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    loaded = load_config()
    assert loaded.config.ports.waloader_port == 9100
    assert loaded.config_path == toml


def test_missing_explicit_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_undocumented_setting_rejected(tmp_path: Path) -> None:
    toml = tmp_path / "waloader.toml"
    toml.write_text("[ports]\nmystery_knob = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml)


def test_invalid_port_range_rejected() -> None:
    with pytest.raises(Exception, match="child_app_end"):
        WALoaderConfig.model_validate(
            {"ports": {"child_app_start": 9000, "child_app_end": 8000}}
        )


def test_db_overrides_apply_and_track_source() -> None:
    loaded = LoadedConfig(config=WALoaderConfig())
    overlaid = apply_db_overrides(
        loaded, {"ports.waloader_port": 9200, "health.interval_seconds": 60}
    )
    assert overlaid.config.ports.waloader_port == 9200
    assert overlaid.config.health.interval_seconds == 60
    assert overlaid.source_of("ports.waloader_port") == "db"
    assert overlaid.source_of("ports.child_app_start") == "default"


def test_db_overrides_cannot_touch_paths() -> None:
    loaded = LoadedConfig(config=WALoaderConfig())
    overlaid = apply_db_overrides(loaded, {"paths.data_dir": "/tmp/evil"})
    assert overlaid.config.paths.data_dir == "data"
    assert not is_db_editable("paths.data_dir")
    assert is_db_editable("ports.waloader_port")


def test_db_override_invalid_value_raises() -> None:
    loaded = LoadedConfig(config=WALoaderConfig())
    with pytest.raises(ConfigError):
        apply_db_overrides(loaded, {"ports.waloader_port": "not-a-port"})


def test_stale_db_override_key_ignored() -> None:
    loaded = LoadedConfig(config=WALoaderConfig())
    overlaid = apply_db_overrides(loaded, {"health.retired_knob": 1})
    assert overlaid.config == loaded.config
