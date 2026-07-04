"""Caddy round-trip: a real deployment served through a real Caddy proxy.

    uv run pytest -m caddy
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from waloader import db as wdb
from waloader.config import load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import users as users_repo
from waloader.services import caddy, deployment, processes

pytestmark = pytest.mark.caddy

BUNDLE = Path("examples/sample-bundle.md")
PUBLIC_PORT = 48080
ADMIN_PORT = 48019


def _uv_cache_dir() -> str | None:
    uv = shutil.which("uv")
    if uv is None:
        return None
    result = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True,
                            timeout=30)
    return result.stdout.strip() or None


def _get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode("utf-8", "replace")
    except Exception:
        return 0, ""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    if shutil.which("caddy") is None:
        pytest.skip("caddy binary not on PATH")
    toml = tmp_path / "waloader.toml"
    cache = _uv_cache_dir()
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[ports]\nchild_app_start = 48641\nchild_app_end = 48660\n"
        f"caddy_public_port = {PUBLIC_PORT}\n"
        f"[caddy]\nenabled = true\nadmin_port = {ADMIN_PORT}\n"
        "[health]\ninitial_check_timeout_seconds = 90\n"
        + (f'[uv]\ncache_dir = "{cache}"\n' if cache else ""),
        encoding="utf-8",
    )
    monkeypatch.setenv("WALOADER_CONFIG", str(toml))
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "e2e", "e2e@example.com", "hash", is_admin=True)
    conn.commit()
    yield config, conn, owner
    caddy.stop(config)
    for app in apps_repo.list_all(conn, include_deleted=True):
        processes.stop_app(conn, config, app)
    conn.close()


class TestProxiedRoundTrip:
    def test_app_served_through_caddy(self, env) -> None:
        config, conn, owner = env

        app, result = deployment.create_app_and_deploy(
            conn, config, owner=owner, name="Client Positions",
            description="", user_mgmt_enabled=False,
            bundle_bytes=BUNDLE.read_bytes(),
        )
        assert result.ok, f"deployment failed:\n{result.error_block()}"
        app = apps_repo.get(conn, app.id)
        assert app.caddy_route == "/apps/client-positions"
        # clean URL advertised, not a port URL
        assert result.url == f"http://localhost:{PUBLIC_PORT}/apps/client-positions"

        started = caddy.start(conn, config)
        assert started.ok, started.output
        try:
            base = f"http://127.0.0.1:{PUBLIC_PORT}"
            deadline = time.monotonic() + 20
            status = 0
            while time.monotonic() < deadline:
                status, _ = _get(f"{base}/apps/client-positions/_stcore/health")
                if status == 200:
                    break
                time.sleep(0.5)
            assert status == 200, "app health endpoint not reachable through caddy"

            page_status, _ = _get(f"{base}/apps/client-positions/")
            assert page_status == 200

            unknown_status, body = _get(f"{base}/nowhere")
            assert unknown_status == 404 and "unknown route" in body

            # deleting/stopping refreshes routes via reload while caddy runs
            reloaded = caddy.reload(conn, config)
            assert reloaded.ok, reloaded.output
        finally:
            stopped = caddy.stop(config)
            assert stopped.ok
        assert not caddy.is_running(config)
