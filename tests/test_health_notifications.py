from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from waloader.config import WALoaderConfig
from waloader.models import App, User
from waloader.notifications import mailer
from waloader.notifications import service as notif_service
from waloader.repositories import apps as apps_repo
from waloader.repositories import notifications as notif_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import health
from waloader.util import utc_now


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_send_mail(*, subject, sender, recipients, html_body):
        calls.append(
            {"subject": subject, "sender": sender,
             "recipients": recipients, "html_body": html_body}
        )

    monkeypatch.setattr(mailer, "send_mail", fake_send_mail)
    return calls


@pytest.fixture
def config(tmp_path: Path) -> WALoaderConfig:
    return WALoaderConfig.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "health": {"grace_period_seconds": 0, "consecutive_failures_threshold": 3},
            "notifications": {"admin_cc": ["ops@corp.com"]},
        }
    )


def _crashed_running_app(conn: sqlite3.Connection, app: App, *, healthy_before=True,
                         started_ago_seconds: int = 3600) -> App:
    """An app that was properly running and whose process has died."""
    apps_repo.set_state(conn, app.id, "running")
    started = (utc_now() - timedelta(seconds=started_ago_seconds)).replace(microsecond=0)
    runtime_repo.upsert_started(conn, app.id, pid=2_111_111, pid_create_time=1.0)
    conn.execute(
        "UPDATE app_runtime SET started_at=?, deployed_healthy=? WHERE app_id=?",
        (started.isoformat(), int(healthy_before), app.id),
    )
    conn.commit()
    return apps_repo.get(conn, app.id)


class TestCrashEmailRules:
    def test_eligible_crash_sends_once(self, conn, config, app: App, user: User,
                                       sent: list) -> None:
        app = _crashed_running_app(conn, app)
        assert notif_service.maybe_send_crash_email(conn, config, app, "process died")
        assert len(sent) == 1
        mail = sent[0]
        assert mail["recipients"] == ["alice@example.com", "ops@corp.com"]
        assert mail["sender"] == "waloader@localhost"
        assert app.name in mail["subject"]
        assert "process died" in mail["html_body"]
        # dedupe: same failure event never emails twice
        assert not notif_service.maybe_send_crash_email(conn, config, app, "process died")
        assert len(sent) == 1

    def test_redeploy_resets_dedupe(self, conn, config, app: App, sent: list) -> None:
        app = _crashed_running_app(conn, app)
        assert notif_service.maybe_send_crash_email(conn, config, app, "x")
        notif_repo.clear_for_app(conn, app.id)  # what a successful deploy does
        assert notif_service.maybe_send_crash_email(conn, config, app, "x")
        assert len(sent) == 2

    def test_never_passed_initial_health_no_email(self, conn, config, app: App,
                                                  sent: list) -> None:
        app = _crashed_running_app(conn, app, healthy_before=False)
        assert not notif_service.maybe_send_crash_email(conn, config, app, "x")
        assert sent == []

    def test_within_grace_period_no_email(self, conn, tmp_path, app: App,
                                          sent: list) -> None:
        config = WALoaderConfig.model_validate(
            {
                "paths": {"data_dir": str(tmp_path / "d")},
                "health": {"grace_period_seconds": 3600},
            }
        )
        app = _crashed_running_app(conn, app, started_ago_seconds=10)
        assert not notif_service.maybe_send_crash_email(conn, config, app, "x")
        assert sent == []

    def test_disabled_no_email(self, conn, tmp_path, app: App, sent: list) -> None:
        config = WALoaderConfig.model_validate(
            {
                "paths": {"data_dir": str(tmp_path / "d")},
                "health": {"grace_period_seconds": 0},
                "notifications": {"crash_emails_enabled": False},
            }
        )
        app = _crashed_running_app(conn, app)
        assert not notif_service.maybe_send_crash_email(conn, config, app, "x")
        assert sent == []

    def test_no_recipients_no_email(self, conn, config, user: User, sent: list) -> None:
        conn.execute("UPDATE users SET email='' WHERE id=?", (user.id,))
        no_cc = config.model_copy(deep=True)
        no_cc.notifications.admin_cc = []
        app = apps_repo.create(conn, owner_id=user.id, name="Q", slug="q")
        conn.commit()
        app = _crashed_running_app(conn, app)
        assert not notif_service.maybe_send_crash_email(conn, no_cc, app, "x")
        assert sent == []

    def test_html_escapes_content(self, conn, config, app: App, sent: list) -> None:
        app = _crashed_running_app(conn, app)
        notif_service.maybe_send_crash_email(conn, config, app, "<script>bad</script>")
        assert "<script>" not in sent[0]["html_body"]
        assert "&lt;script&gt;" in sent[0]["html_body"]


class TestHealthCheckService:
    def test_healthy_check_records(self, conn, config, app: App, sent: list) -> None:
        app = _crashed_running_app(conn, app)  # state running; probe faked healthy

        outcome = health.check_app(
            conn, config, app, _prober=lambda c, a, process_alive: health.ProbeResult(True, "")
        )
        assert outcome.healthy and not outcome.marked_failed
        rt = runtime_repo.get(conn, app.id)
        assert rt.last_healthy_at is not None and rt.consecutive_failures == 0

    def test_dead_process_fails_immediately_and_emails(
        self, conn, config, app: App, sent: list
    ) -> None:
        app = _crashed_running_app(conn, app)  # pid 2111111 is dead
        outcome = health.check_app(conn, config, app)
        assert not outcome.healthy
        assert outcome.marked_failed
        assert outcome.email_sent and len(sent) == 1
        assert apps_repo.get(conn, app.id).state == "failed"

    def test_transient_unhealthy_needs_threshold(
        self, conn, config, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _crashed_running_app(conn, app)
        # keep the process "alive" but the probe unhealthy
        from waloader.services import processes

        monkeypatch.setattr(processes, "is_app_running", lambda c, a: True)

        def unhealthy(c, a, process_alive):
            return health.ProbeResult(False, "HTTP health endpoint not responding")

        first = health.check_app(conn, config, app, _prober=unhealthy)
        second = health.check_app(conn, config, app, _prober=unhealthy)
        assert not first.marked_failed and not second.marked_failed
        third = health.check_app(conn, config, app, _prober=unhealthy)
        assert third.marked_failed
        assert apps_repo.get(conn, app.id).state == "failed"

    def test_check_all_only_touches_running(self, conn, config, user: User) -> None:
        a = apps_repo.create(conn, owner_id=user.id, name="R", slug="r")
        apps_repo.set_state(conn, a.id, "running")
        runtime_repo.upsert_started(conn, a.id, pid=2_111_111, pid_create_time=1.0)
        b = apps_repo.create(conn, owner_id=user.id, name="S", slug="s")
        apps_repo.set_state(conn, b.id, "stopped")
        conn.commit()
        outcomes = health.check_all_running(conn, config)
        assert [o.slug for o in outcomes] == ["r"]


class TestProbePrimitives:
    def test_urls(self, config, app: App, tmp_path) -> None:
        apps_with_port = app.__class__(**{**app.__dict__, "port": 8765})
        assert health.health_url(config, apps_with_port) == (
            "http://127.0.0.1:8765/_stcore/health"
        )
        assert health.app_url(config, apps_with_port) == "http://localhost:8765"
        caddy_config = WALoaderConfig.model_validate(
            {
                "paths": {"data_dir": str(tmp_path / "d")},
                "caddy": {"enabled": True},
                "server": {"public_host": "finbox"},
            }
        )
        assert health.health_url(caddy_config, apps_with_port) == (
            f"http://127.0.0.1:8765/apps/{app.slug}/_stcore/health"
        )
        assert health.app_url(caddy_config, apps_with_port) == (
            f"http://finbox:8080/apps/{app.slug}"
        )

    def test_port_open_false_on_closed_port(self) -> None:
        assert not health.port_open(47999, timeout=0.2)

    def test_probe_app_short_circuits(self, config, app: App) -> None:
        result = health.probe_app(config, app, process_alive=False)
        assert not result.healthy and result.reason == "process not running"
        result = health.probe_app(config, app, process_alive=True)  # port is None
        assert not result.healthy and result.reason == "no allocated port"
