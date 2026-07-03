from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from waloader.config import WALoaderConfig
from waloader.services import preflight, uv_env


def _config(tmp_path: Path, **uv_overrides) -> WALoaderConfig:
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\n")
    return WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "executables": {"uv_binary": str(fake_uv)},
            "uv": uv_overrides,
        }
    )


class TestRedaction:
    def test_url_credentials(self) -> None:
        text = "https://user:s3cret@repo.corp.com/simple/ failed"
        assert uv_env.redact(text) == "https://***@repo.corp.com/simple/ failed"

    def test_token_params(self) -> None:
        assert (
            uv_env.redact("GET https://x.com/f.whl?token=abc123&x=1")
            == "GET https://x.com/f.whl?token=***&x=1"
        )
        assert uv_env.redact("?api_key=zzz") == "?api_key=***"

    def test_plain_text_untouched(self) -> None:
        assert uv_env.redact("no secrets here") == "no secrets here"


class TestBuildEnv:
    def test_all_settings_exported(self, tmp_path: Path) -> None:
        config = _config(
            tmp_path,
            config_file="/corp/uv.toml",
            system_certs=True,
            ssl_cert_file="/corp/ca.pem",
            ssl_cert_dir="/corp/certs",
        )
        env = uv_env.build_env(config, base_env={"PATH": "/usr/bin", "KEEP": "me"})
        assert env["UV_CONFIG_FILE"] == "/corp/uv.toml"
        assert env["UV_SYSTEM_CERTS"] == "true"
        assert env["SSL_CERT_FILE"] == "/corp/ca.pem"
        assert env["SSL_CERT_DIR"] == "/corp/certs"
        assert env["UV_CACHE_DIR"] == str(config.uv_cache_dir)
        assert env["KEEP"] == "me"  # existing environment preserved

    def test_unset_settings_not_exported(self, tmp_path: Path) -> None:
        env = uv_env.build_env(_config(tmp_path), base_env={"PATH": "/usr/bin"})
        assert "UV_CONFIG_FILE" not in env
        assert "UV_SYSTEM_CERTS" not in env
        assert "SSL_CERT_FILE" not in env
        assert "UV_CACHE_DIR" in env  # always derived


class TestUvCommand:
    def test_command_with_python_and_insecure_hosts(self, tmp_path: Path) -> None:
        config = _config(tmp_path, allow_insecure_hosts=["repo.internal"])
        command = uv_env.uv_command(
            config, "pip", "install", "--dry-run", "pandas", python="/py312"
        )
        assert command[1:] == [
            "pip", "install", "--dry-run", "pandas",
            "--python", "/py312",
            "--allow-insecure-host", "repo.internal",
        ]

    def test_missing_uv_raises(self, tmp_path: Path) -> None:
        config = WALoaderConfig.model_validate(
            {
                "paths": {"data_dir": str(tmp_path / "data")},
                "executables": {"uv_binary": str(tmp_path / "missing-uv")},
            }
        )
        with pytest.raises(uv_env.UvNotFoundError):
            uv_env.uv_command(config, "venv")

    def test_describe_redacts_and_names_env(self, tmp_path: Path) -> None:
        config = _config(tmp_path, config_file="/corp/uv.toml")
        env = uv_env.build_env(config, base_env={})
        display = uv_env.describe_command(
            ["uv", "pip", "install", "https://u:p@h/x"], env
        )
        assert "UV_CONFIG_FILE" in display
        assert "u:p@" not in display and "***@h/x" in display


class TestVenvPython:
    def test_platform_layout(self, tmp_path: Path) -> None:
        python = uv_env.venv_python(tmp_path / "venv")
        if sys.platform == "win32":
            assert python == tmp_path / "venv" / "Scripts" / "python.exe"
        else:
            assert python == tmp_path / "venv" / "bin" / "python"


class TestPreflightUnit:
    def _ok_runner(self, calls: list) -> object:
        def runner(command, env, timeout):
            calls.append((command, env))
            return SimpleNamespace(returncode=0, stdout="Would install pandas", stderr="")

        return runner

    def test_creates_venv_then_dry_runs(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        calls: list = []
        result = preflight.run_preflight(config, runner=self._ok_runner(calls))
        assert result.ok
        assert len(calls) == 2
        venv_cmd, dry_cmd = calls[0][0], calls[1][0]
        assert venv_cmd[1] == "venv"
        assert dry_cmd[1:4] == ["pip", "install", "--dry-run"]
        assert "pandas" in dry_cmd
        assert "UV_CACHE_DIR" in calls[1][1]

    def test_failure_output_redacted(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        # pre-create the venv marker so only the dry-run call happens
        marker = uv_env.venv_python(config.tmp_dir / "preflight-venv")
        marker.parent.mkdir(parents=True)
        marker.write_text("")

        def runner(command, env, timeout):
            return SimpleNamespace(
                returncode=1, stdout="", stderr="401 at https://u:tok@corp/simple"
            )

        result = preflight.run_preflight(config, runner=runner)
        assert not result.ok and result.returncode == 1
        assert "u:tok@" not in result.output and "***@corp" in result.output

    def test_timeout_reported(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        def runner(command, env, timeout):
            raise subprocess.TimeoutExpired(command, timeout)

        result = preflight.run_preflight(config, runner=runner)
        assert not result.ok and "timed out" in result.output

    def test_missing_uv_reported_not_raised(self, tmp_path: Path) -> None:
        config = WALoaderConfig.model_validate(
            {
                "paths": {"data_dir": str(tmp_path / "data")},
                "executables": {"uv_binary": str(tmp_path / "missing-uv")},
            }
        )
        result = preflight.run_preflight(config)
        assert not result.ok and "not found" in result.output


@pytest.mark.integration
class TestPreflightIntegration:
    def test_real_preflight_against_index(self, tmp_path: Path) -> None:
        if shutil.which("uv") is None:
            pytest.skip("uv not on PATH")
        config = WALoaderConfig.model_validate(
            {"paths": {"data_dir": str(tmp_path / "data")}}
        )
        result = preflight.run_preflight(config)
        assert result.ok, f"preflight failed:\n{result.command_display}\n{result.output}"
        assert "pandas" in result.output.lower() or result.returncode == 0
