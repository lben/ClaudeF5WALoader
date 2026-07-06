"""End-to-end: a REAL deployment of examples/sample-bundle.md on this machine.

No seams: real uv resolve+install into a per-version venv, a real detached
Streamlit child process, real HTTP health checks — then datasets, update,
stop, delete, reconcile. Needs uv + network (first run populates the cache).

    uv run pytest -m e2e
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from waloader import db as wdb
from waloader.config import WALoaderConfig, load_config
from waloader.repositories import apps as apps_repo
from waloader.repositories import deployments as deployments_repo
from waloader.repositories import users as users_repo
from waloader.services import (
    datasets_service,
    deletion,
    deployment,
    health,
    layout,
    lifecycle,
    processes,
    reconciliation,
)

pytestmark = pytest.mark.e2e

BUNDLE = Path("examples/sample-bundle.md")


def _uv_cache_dir() -> str | None:
    """Reuse the machine's warm uv cache so e2e doesn't re-download wheels."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    result = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True,
                            timeout=30)
    return result.stdout.strip() or None


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory):
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    tmp_path = tmp_path_factory.mktemp("e2e")
    toml = tmp_path / "waloader.toml"
    cache = _uv_cache_dir()
    toml.write_text(
        f'[paths]\ndata_dir = "{tmp_path / "data"}"\n'
        "[ports]\nchild_app_start = 48601\nchild_app_end = 48640\n"
        "[health]\ninitial_check_timeout_seconds = 90\n"
        + (f'[uv]\ncache_dir = "{cache}"\n' if cache else ""),
        encoding="utf-8",
    )
    import os

    old = os.environ.get("WALOADER_CONFIG")
    os.environ["WALOADER_CONFIG"] = str(toml)
    config = load_config().config
    conn = wdb.connect(config.database_path)
    wdb.migrate(conn)
    owner = users_repo.create(conn, "e2e", "e2e@example.com", "hash", is_admin=True)
    conn.commit()
    yield config, conn, owner
    for app in apps_repo.list_all(conn, include_deleted=True):
        processes.stop_app(conn, config, app)
    conn.close()
    if old is None:
        os.environ.pop("WALOADER_CONFIG", None)
    else:
        os.environ["WALOADER_CONFIG"] = old


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status == 200
    except Exception:
        return False


class TestFullDeploymentRoundTrip:
    def test_the_whole_journey(self, env: tuple[WALoaderConfig, object, object]) -> None:
        config, conn, owner = env
        bundle_bytes = BUNDLE.read_bytes()

        # -- create + deploy for real ------------------------------------
        app, result = deployment.create_app_and_deploy(
            conn, config, owner=owner, name="Client Positions",
            description="e2e", user_mgmt_enabled=False, bundle_bytes=bundle_bytes,
        )
        assert result.ok, f"deployment failed:\n{result.error_block()}"
        assert app.state == "running"
        assert app.slug == "client-positions"
        assert result.version_number == 1

        # the app's own tests ran during deployment
        assert any(s.name == "app tests" and s.ok and "no tests" not in s.output
                   for s in result.steps)

        # real HTTP: streamlit health endpoint + the page itself
        assert _http_ok(f"http://127.0.0.1:{app.port}/_stcore/health")
        assert _http_ok(f"http://127.0.0.1:{app.port}/")

        # -- datasets round trip -----------------------------------------
        # the bundle declares dataset_concepts = ["clients"]: auto-created
        from waloader.repositories import datasets as datasets_repo

        concept = datasets_repo.get_concept_by_name(conn, app.id, "clients")
        assert concept is not None, "bundle-declared concept was not auto-created"
        df1 = pd.DataFrame({"client": ["Acme", "Globex"], "aum_musd": [1.0, 2.0]})
        datasets_service.store_upload(
            conn, config, app, concept, filename="clients.csv",
            data=df1.to_csv(index=False).encode(),
        )
        df2 = pd.DataFrame({"client": ["Acme"], "region": ["EMEA"]})
        diff = datasets_service.replacement_diff(
            conn, config, concept, "clients2.csv",
            df2.to_csv(index=False).encode(), None,
        )
        assert diff.has_changes and "region" in diff.added
        datasets_service.store_upload(
            conn, config, app, concept, filename="clients2.csv",
            data=df2.to_csv(index=False).encode(),
        )

        # -- update (same pipeline, v2, same port, new process) ------------
        old_port = app.port
        update = deployment.redeploy(conn, config, apps_repo.get(conn, app.id),
                                     bundle_bytes, actor_id=owner.id)
        assert update.ok, f"update failed:\n{update.error_block()}"
        app = apps_repo.get(conn, app.id)
        assert app.current_version == 2 and app.port == old_port
        deadline = time.monotonic() + 30
        while not _http_ok(f"http://127.0.0.1:{app.port}/_stcore/health"):
            assert time.monotonic() < deadline, "updated app never became healthy"
            time.sleep(1)
        # only the current venv remains
        assert [p.name for p in layout.venvs_root(config, app.slug).iterdir()] == [
            "000002"
        ]

        # -- stop / resume --------------------------------------------------
        assert lifecycle.stop(conn, config, app).ok
        app = apps_repo.get(conn, app.id)
        assert app.state == "stopped"
        assert not _http_ok(f"http://127.0.0.1:{app.port}/_stcore/health", timeout=2)

        resume = lifecycle.start(conn, config, app)
        assert resume.ok, resume.message
        app = apps_repo.get(conn, app.id)
        assert app.state == "running"
        assert _http_ok(f"http://127.0.0.1:{app.port}/_stcore/health")

        # -- health check service against the live process ------------------
        outcome = health.check_app(conn, config, app)
        assert outcome.healthy

        # -- delete -> archive ------------------------------------------------
        archive = deletion.soft_delete_app(conn, config, app)
        assert archive.exists()
        assert apps_repo.list_all(conn) == []
        assert not _http_ok(f"http://127.0.0.1:{app.port or 0}/_stcore/health",
                            timeout=2)

        # -- reconcile leaves a clean world -----------------------------------
        report = reconciliation.reconcile(conn, config)
        assert report.actions == []

        history = deployments_repo.list_for_app(conn, app.id)
        assert [d.status for d in history] == ["succeeded", "succeeded"]
