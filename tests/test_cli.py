from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from waloader import db as wdb
from waloader.config import WALoaderConfig, load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.tools import appctl, caddyctl, doctor, maintenance, serve
from waloader.tools import db as db_cli
from waloader.tools import users as users_cli


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WALoaderConfig:
    """A config file + WALOADER_CONFIG env var, as a real operator would have."""
    toml = tmp_path / "waloader.toml"
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[ports]\nchild_app_start = 47900\nchild_app_end = 47910\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    return load_config().config


@pytest.fixture
def seeded(cli_env: WALoaderConfig) -> sqlite3.Connection:
    conn = wdb.connect(cli_env.database_path)
    wdb.migrate(conn)
    user = users_repo.create(conn, "alice", "a@x.com", "hash", is_admin=True)
    apps_repo.create(conn, owner_id=user.id, name="Demo", slug="demo")
    conn.commit()
    yield conn
    conn.close()


class TestDbCli:
    def test_migrate_status_backup(self, cli_env, capsys) -> None:
        assert db_cli.main(["migrate"]) == 0
        assert "applied 001_initial" in capsys.readouterr().out
        assert db_cli.main(["migrate"]) == 0
        assert "up to date" in capsys.readouterr().out
        assert db_cli.main(["status"]) == 0
        assert "001_initial: applied" in capsys.readouterr().out
        assert db_cli.main(["backup"]) == 0
        assert "backup created" in capsys.readouterr().out
        assert list(cli_env.backups_dir.glob("waloader-*.db"))


class TestUsersCli:
    def test_create_list_reset(self, cli_env, capsys) -> None:
        assert users_cli.main(
            ["create-admin", "boss", "--email", "b@x.com", "--password", "long-enough-pw"]
        ) == 0
        assert "admin 'boss' created" in capsys.readouterr().out
        assert users_cli.main(["list"]) == 0
        out = capsys.readouterr().out
        assert "boss" in out and "admin" in out
        assert users_cli.main(
            ["reset-password", "boss", "--password", "another-long-pw"]
        ) == 0

    def test_weak_password_fails(self, cli_env) -> None:
        with pytest.raises(SystemExit):
            users_cli.main(["create-admin", "boss", "--password", "short"])

    def test_reset_unknown_user_fails(self, cli_env) -> None:
        with pytest.raises(SystemExit):
            users_cli.main(["reset-password", "ghost", "--password", "long-enough-pw"])


class TestAppctl:
    def test_list_empty(self, cli_env, capsys) -> None:
        assert appctl.main(["list"]) == 0
        assert "no apps" in capsys.readouterr().out

    def test_list_and_status(self, seeded, capsys) -> None:
        assert appctl.main(["list"]) == 0
        assert "demo" in capsys.readouterr().out
        assert appctl.main(["status", "demo"]) == 0
        out = capsys.readouterr().out
        assert "state:        created" in out
        assert "Demo (demo)" in out

    def test_unknown_slug_fails(self, seeded) -> None:
        with pytest.raises(SystemExit):
            appctl.main(["status", "ghost"])

    def test_health_unhealthy_exit_code(self, seeded, capsys) -> None:
        assert appctl.main(["health", "demo"]) == 1
        assert "UNHEALTHY" in capsys.readouterr().out

    def test_start_without_version_fails(self, seeded, capsys) -> None:
        assert appctl.main(["start", "demo"]) == 1
        assert "no deployed version" in capsys.readouterr().out

    def test_reconcile(self, seeded, capsys) -> None:
        assert appctl.main(["reconcile"]) == 0
        assert "checked 1 app(s)" in capsys.readouterr().out


class TestCaddyctl:
    def test_generate_and_status(self, seeded, capsys) -> None:
        assert caddyctl.main(["generate"]) == 0
        assert "caddyfile written" in capsys.readouterr().out
        assert caddyctl.main(["status"]) == 0
        out = capsys.readouterr().out
        assert "running" in out and "config_exists" in out

    @pytest.mark.caddy
    def test_validate_with_real_binary(self, seeded, capsys) -> None:
        import shutil

        if shutil.which("caddy") is None:
            pytest.skip("caddy binary not on PATH")
        caddyctl.main(["generate"])
        capsys.readouterr()
        assert caddyctl.main(["validate"]) == 0


class TestMaintenanceCli:
    def test_run_all_and_pieces(self, seeded, capsys) -> None:
        assert maintenance.main(["backup-db"]) == 0
        assert "backup created" in capsys.readouterr().out
        assert maintenance.main(["cleanup-logs"]) == 0
        assert maintenance.main(["cleanup-retention"]) == 0
        assert maintenance.main(["hard-delete-expired-apps"]) == 0
        assert maintenance.main(["archive-deleted-apps"]) == 0
        assert "all deleted apps have archives" in capsys.readouterr().out
        assert maintenance.main(["run-all"]) == 0
        assert "backup:" in capsys.readouterr().out


class TestServe:
    def test_build_command_direct_mode(self, cli_env) -> None:
        command = serve.build_serve_command(cli_env)
        joined = " ".join(command)
        assert "-m streamlit run" in joined
        assert "--server.port 8501" in joined
        assert "--server.address 0.0.0.0" in joined
        assert "--baseUrlPath" not in joined
        assert "app.py" in joined

    def test_build_command_caddy_mode(self, tmp_path) -> None:
        config = WALoaderConfig.model_validate(
            {"paths": {"data_dir": str(tmp_path / "d")}, "caddy": {"enabled": True}}
        )
        joined = " ".join(serve.build_serve_command(config))
        assert "--server.baseUrlPath waloader" in joined
        assert "--server.address 127.0.0.1" in joined

    def test_print_command_runs_reconcile_without_launching(
        self, seeded, capsys
    ) -> None:
        assert serve.main(["--print-command"]) == 0
        out = capsys.readouterr().out
        assert "WALoader UI: http://localhost:8501" in out
        assert "streamlit" in out


class TestDoctor:
    def test_offline_doctor_passes_here(self, cli_env, capsys) -> None:
        assert doctor.main(["--offline"]) == 0
        out = capsys.readouterr().out
        assert "all checks passed" in out
        assert "[✓] config" in out
        assert "[-] uv preflight" in out  # skipped offline

    def test_bad_python_binary_fails(self, tmp_path, monkeypatch, capsys) -> None:
        toml = tmp_path / "waloader.toml"
        toml.write_text(
            f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
            '[executables]\npython_binary = "/nonexistent/python312"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("WALOADER_CONFIG", str(toml))
        assert doctor.main(["--offline"]) == 1
        out = capsys.readouterr().out
        assert "[✗] python binary" in out and "doctor: FAILED" in out

    @pytest.mark.integration
    def test_full_doctor_with_network(self, cli_env, capsys) -> None:
        assert doctor.main([]) == 0
        assert "uv preflight" in capsys.readouterr().out
