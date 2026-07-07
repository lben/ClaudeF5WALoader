"""Safety-critical unit tests for the update tool: it must never overwrite or
delete runtime state (config/waloader.toml, data/, .venv) even under a
naive/hostile payload, and must clean up files removed between versions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from waloader.tools import deploy


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_install(root: Path, *, with_runtime: bool = True) -> None:
    """A minimal WALoader-looking project tree."""
    (root / "src" / "waloader").mkdir(parents=True)
    (root / "src" / "waloader" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n', encoding="utf-8"
    )
    (root / "pyproject.toml").write_text("[project]\nname='waloader'\n",
                                         encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "waloader.example.toml").write_text("# template\n",
                                                           encoding="utf-8")
    if with_runtime:
        (root / "config" / "waloader.toml").write_text(
            "MY REAL SERVER CONFIG\n", encoding="utf-8"
        )
        (root / "data").mkdir()
        (root / "data" / "waloader.db").write_text("PRECIOUS DB\n",
                                                   encoding="utf-8")
        (root / ".venv").mkdir()
        (root / ".venv" / "marker").write_text("venv\n", encoding="utf-8")


class TestProtection:
    def test_is_protected(self) -> None:
        assert deploy.is_protected("config/waloader.toml")
        assert deploy.is_protected("config/deploy.toml")
        assert deploy.is_protected("data/waloader.db")
        assert deploy.is_protected(".venv/bin/python")
        assert deploy.is_protected(".git/config")
        assert not deploy.is_protected("config/waloader.example.toml")
        assert not deploy.is_protected("src/waloader/__init__.py")

    def test_safe_target_rejects_escape_and_protected(self, tmp_path: Path) -> None:
        with pytest.raises(deploy.DeployError):
            deploy.safe_target(tmp_path, "../escape.py")
        with pytest.raises(deploy.DeployError):
            deploy.safe_target(tmp_path, "config/waloader.toml")
        assert deploy.safe_target(tmp_path, "src/app.py") == (
            tmp_path / "src" / "app.py"
        ).resolve()


class TestPayloadSelection:
    def test_git_payload_excludes_runtime_state(self, tmp_path: Path) -> None:
        _make_install(tmp_path, with_runtime=True)
        _git(tmp_path, "init")
        _git(tmp_path, "config", "user.email", "t@t.t")
        _git(tmp_path, "config", "user.name", "t")
        # add source + example, but data/config.toml/.venv are gitignored...
        (tmp_path / ".gitignore").write_text(
            "data/\nconfig/waloader.toml\n.venv/\n", encoding="utf-8"
        )
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-m", "init")

        files = deploy.compute_payload_files(tmp_path, use_git=True)
        assert "src/waloader/__init__.py" in files
        assert "config/waloader.example.toml" in files
        assert "config/waloader.toml" not in files  # gitignored AND protected
        assert not any(f.startswith("data/") for f in files)
        assert not any(f.startswith(".venv/") for f in files)

    def test_force_added_protected_still_excluded(self, tmp_path: Path) -> None:
        _make_install(tmp_path, with_runtime=True)
        _git(tmp_path, "init")
        _git(tmp_path, "config", "user.email", "t@t.t")
        _git(tmp_path, "config", "user.name", "t")
        _git(tmp_path, "add", "-A", "-f")  # force-add EVERYTHING incl data/config
        _git(tmp_path, "commit", "-m", "oops")
        files = deploy.compute_payload_files(tmp_path, use_git=True)
        # the denylist saves us even though git tracked them
        assert "config/waloader.toml" not in files
        assert not any(f.startswith("data/") for f in files)

    def test_no_git_walk_excludes_runtime(self, tmp_path: Path) -> None:
        _make_install(tmp_path, with_runtime=True)
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "x.pyc").write_text("", encoding="utf-8")
        files = deploy.compute_payload_files(tmp_path, use_git=False)
        assert "src/waloader/__init__.py" in files
        assert "config/waloader.toml" not in files
        assert not any(f.startswith("data/") for f in files)
        assert not any("__pycache__" in f for f in files)


class TestManifestDiff:
    def test_deletions_never_include_protected(self) -> None:
        old = {"files": {"a.py": "1", "old.py": "2", "data/db": "3"}}
        new = {"files": {"a.py": "1"}}
        assert deploy.plan_deletions(old, new) == ["old.py"]  # not data/db

    def test_no_old_manifest_means_no_deletions(self) -> None:
        assert deploy.plan_deletions({}, {"files": {"a.py": "1"}}) == []


class TestPackageApplyRoundTrip:
    def _package(self, src: Path, out: Path) -> None:
        deploy.create_package(src, out, use_git=False)

    def test_apply_updates_code_preserves_runtime_deletes_stale(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "src_repo"
        target = tmp_path / "server"
        _make_install(source, with_runtime=False)
        (source / "src" / "waloader" / "keep.py").write_text("v1\n",
                                                             encoding="utf-8")
        (source / "src" / "waloader" / "goodbye.py").write_text("bye\n",
                                                               encoding="utf-8")

        _make_install(target, with_runtime=True)  # server has real config + data
        (target / "src" / "waloader" / "goodbye.py").write_text("bye\n",
                                                               encoding="utf-8")

        # first apply: establishes baseline, updates code, preserves runtime
        pkg1 = tmp_path / "p1.tar.gz"
        self._package(source, pkg1)
        deploy.apply_package(
            target, pkg1, run_uv=False, run_migrate=False, restart=False,
        )
        assert (target / "src" / "waloader" / "keep.py").read_text() == "v1\n"
        # runtime state untouched
        assert (target / "config" / "waloader.toml").read_text() == \
            "MY REAL SERVER CONFIG\n"
        assert (target / "data" / "waloader.db").read_text() == "PRECIOUS DB\n"
        assert (target / ".venv" / "marker").exists()
        assert (target / ".deploy" / "manifest.json").exists()

        # second version: keep.py changes, goodbye.py removed from the repo
        (source / "src" / "waloader" / "keep.py").write_text("v2\n",
                                                             encoding="utf-8")
        (source / "src" / "waloader" / "goodbye.py").unlink()
        pkg2 = tmp_path / "p2.tar.gz"
        self._package(source, pkg2)
        report2 = deploy.apply_package(
            target, pkg2, run_uv=False, run_migrate=False, restart=False,
        )
        assert (target / "src" / "waloader" / "keep.py").read_text() == "v2\n"
        # stale file cleaned up via manifest diff (naive unzip can't do this)
        assert not (target / "src" / "waloader" / "goodbye.py").exists()
        assert "src/waloader/goodbye.py" in report2["deletions"]
        # runtime STILL untouched after a second round
        assert (target / "config" / "waloader.toml").read_text() == \
            "MY REAL SERVER CONFIG\n"
        assert (target / "data" / "waloader.db").read_text() == "PRECIOUS DB\n"

    def test_apply_refuses_non_install_target(self, tmp_path: Path) -> None:
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        pkg = tmp_path / "p.tar.gz"
        self._package(source, pkg)
        empty = tmp_path / "not-an-install"
        empty.mkdir()
        with pytest.raises(deploy.DeployError, match="does not look like"):
            deploy.apply_package(empty, pkg, run_uv=False, run_migrate=False,
                                 restart=False)

    def test_apply_aborts_on_missing_sentinels(self, tmp_path: Path) -> None:
        # a payload without src/waloader/__init__.py must never be applied
        bad_src = tmp_path / "bad"
        (bad_src / "docs").mkdir(parents=True)
        (bad_src / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (bad_src / "docs" / "x.md").write_text("hi\n", encoding="utf-8")
        pkg = tmp_path / "bad.tar.gz"
        deploy.create_package(bad_src, pkg, use_git=False)

        target = tmp_path / "server"
        _make_install(target, with_runtime=True)
        with pytest.raises(deploy.DeployError, match="sentinel"):
            deploy.apply_package(target, pkg, run_uv=False, run_migrate=False,
                                 restart=False)
        # nothing was touched
        assert (target / "data" / "waloader.db").read_text() == "PRECIOUS DB\n"

    def test_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        source = tmp_path / "s"
        target = tmp_path / "t"
        _make_install(source, with_runtime=False)
        (source / "src" / "waloader" / "new.py").write_text("new\n",
                                                            encoding="utf-8")
        _make_install(target, with_runtime=True)
        pkg = tmp_path / "p.tar.gz"
        self._package(source, pkg)
        report = deploy.apply_package(
            target, pkg, dry_run=True, run_uv=False, run_migrate=False,
            restart=False,
        )
        assert report["dry_run"] is True
        assert "src/waloader/new.py" in report["updates"]
        assert not (target / "src" / "waloader" / "new.py").exists()  # untouched
        assert "steps" not in report


class TestPushDryRun:
    def test_push_dry_run_needs_no_ssh(self, tmp_path: Path, capsys) -> None:
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        rc = deploy.push(
            source,
            {"host": "box", "remote_dir": "/srv/waloader", "uv": "uv"},
            use_git=False, dry_run=True,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "remote apply:" in out
        assert "/srv/waloader" in out and "--restart" in out

    def test_push_requires_host_and_remote_dir(self, tmp_path: Path) -> None:
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        with pytest.raises(deploy.DeployError, match="remote_dir"):
            deploy.push(source, {"host": "box"}, use_git=False, dry_run=True)


class TestUvEnvironment:
    def test_auto_reads_uv_config_from_waloader_toml(self, tmp_path: Path) -> None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "waloader.toml").write_text(
            "[paths]\ndata_dir = 'data'\n"
            "[uv]\n"
            'config_file = "/home/bl/uv.toml"   # corporate index\n'
            "system_certs = true\n"
            'ssl_cert_file = "/etc/pki/ca.crt"\n',
            encoding="utf-8",
        )
        env = deploy._auto_uv_env(tmp_path)
        assert env["UV_CONFIG_FILE"] == "/home/bl/uv.toml"  # comment stripped
        assert env["UV_SYSTEM_CERTS"] == "true"
        assert env["SSL_CERT_FILE"] == "/etc/pki/ca.crt"

    def test_auto_env_ignores_other_sections_and_missing(self, tmp_path: Path) -> None:
        assert deploy._auto_uv_env(tmp_path) == {}  # no config file
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "waloader.toml").write_text(
            "[executables]\nconfig_file = '/not/uv'\n", encoding="utf-8"
        )
        assert deploy._auto_uv_env(tmp_path) == {}  # config_file not under [uv]

    def test_uv_sync_runs_with_derived_env_and_override_wins(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = tmp_path / "server"
        _make_install(root, with_runtime=True)
        (root / "config" / "waloader.toml").write_text(
            "[uv]\nconfig_file = '/box/uv.toml'\nsystem_certs = true\n",
            encoding="utf-8",
        )
        seen = {}

        def fake_run(cmd, cwd=None, env=None, **kw):
            if cmd[:2] == ["uv", "sync"]:
                seen["env"] = env
            class _R:
                returncode = 0
                stdout = ""
            return _R()

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        # a payload to apply
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        pkg = tmp_path / "p.tar.gz"
        deploy.create_package(source, pkg, use_git=False)

        deploy.apply_package(
            root, pkg, uv="uv",
            env_overrides={"UV_CONFIG_FILE": "/override/uv.toml"},
            run_uv=True, run_migrate=False, restart=False,
        )
        assert seen["env"]["UV_CONFIG_FILE"] == "/override/uv.toml"  # override wins
        assert seen["env"]["UV_SYSTEM_CERTS"] == "true"              # auto-derived


class TestPushBinaries:
    def test_missing_ssh_gives_actionable_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        monkeypatch.setattr(deploy.shutil, "which", lambda name: None)
        with pytest.raises(deploy.DeployError, match="not found on PATH"):
            deploy.push(
                source, {"host": "box", "remote_dir": "/srv/waloader"},
                use_git=False,
            )

    def test_absolute_ssh_path_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(deploy.DeployError, match="binary not found at"):
            deploy._require_binary(str(tmp_path / "nope" / "ssh.exe"), "ssh")

    def test_scp_uses_bare_filenames_not_windows_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Regression: scp reads C:\\...\\payload as host 'C'. We must scp bare
        filenames from cwd=staging instead."""
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        calls = []

        class _R:
            returncode = 0

        def fake_run(cmd, cwd=None):
            calls.append((list(cmd), cwd))
            return _R()

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        monkeypatch.setattr(deploy.shutil, "which", lambda name: "/usr/bin/" + name)

        rc = deploy.push(
            source,
            {"host": "box", "remote_dir": "/srv/waloader", "uv": "uv",
             "env": {"UV_CONFIG_FILE": "/box/uv.toml", "UV_SYSTEM_CERTS": "true"}},
            use_git=False,
        )
        assert rc == 0
        # the remote apply command must carry the env overrides
        apply_calls = [c for c in calls if "bash -lc" in " ".join(c[0])]
        assert apply_calls
        joined = " ".join(apply_calls[0][0])
        assert "UV_CONFIG_FILE=/box/uv.toml" in joined
        assert "UV_SYSTEM_CERTS=true" in joined
        scp_calls = [c for c in calls if c[0][0].endswith("scp")]
        assert len(scp_calls) == 1
        argv, cwd = scp_calls[0]
        assert "payload.tar.gz" in argv and "deploy.py" in argv
        assert cwd is not None                       # ran from the staging dir
        assert not any("\\" in a or a[1:2] == ":" for a in argv)  # no C:\ paths
        assert argv[-1] == "box:/srv/waloader/.deploy/incoming/"

    def test_custom_ssh_scp_binaries_respected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        source = tmp_path / "s"
        _make_install(source, with_runtime=False)
        calls = []

        class _R:
            returncode = 0

        monkeypatch.setattr(deploy.subprocess, "run",
                            lambda cmd, cwd=None: calls.append(list(cmd)) or _R())
        monkeypatch.setattr(deploy.shutil, "which", lambda name: name)
        monkeypatch.setattr(Path, "exists", lambda self: True)

        deploy.push(
            source,
            {"host": "box", "remote_dir": "/srv/w",
             "ssh": "C:/Program Files/Git/usr/bin/ssh.exe",
             "scp": "C:/Program Files/Git/usr/bin/scp.exe"},
            use_git=False,
        )
        assert any(c[0].endswith("ssh.exe") for c in calls)
        assert any(c[0].endswith("scp.exe") for c in calls)
