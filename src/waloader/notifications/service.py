"""Crash notification rules (G01 §4.14).

Never notified: dependency/test/initial-deployment failures, user-triggered
stop/restart, update failures — structurally, because this function is only
called on a health-loop ``running -> failed`` transition. On top of that it
enforces: initial health was passed (deployed_healthy), the production grace
period elapsed, emails are enabled, and one email per failure event (dedupe
key = process start identity, cleared on the next successful deploy/start).
"""

from __future__ import annotations

import html
import logging
import sqlite3
from datetime import timedelta

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.notifications import mailer
from waloader.repositories import audit as audit_repo
from waloader.repositories import notifications as notif_repo
from waloader.repositories import runtime as runtime_repo
from waloader.repositories import users as users_repo
from waloader.util import parse_iso, utc_now

log = logging.getLogger(__name__)


def build_crash_email_html(app: App, reason: str, *, last_healthy_at: str | None) -> str:
    """Simple, Outlook-compatible HTML (tables + inline styles, no CSS classes)."""
    e = html.escape
    rows = [
        ("App", f"{e(app.name)} ({e(app.slug)})"),
        ("What happened", e(reason)),
        ("Last healthy", e(last_healthy_at or "unknown")),
        ("State", "failed"),
        ("Runtime log",
         f"data/logs/apps/{e(app.slug)}/{app.current_version or 0:06d}/runtime.log"),
    ]
    table = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;font-weight:bold;'
        f'font-family:Segoe UI,Arial,sans-serif">{k}</td>'
        f'<td style="padding:4px 0;font-family:Segoe UI,Arial,sans-serif">{v}</td></tr>'
        for k, v in rows
    )
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
        f"<p>Your WALoader app <b>{e(app.name)}</b> stopped running.</p>"
        f'<table cellspacing="0" cellpadding="0">{table}</table>'
        "<p>Open WALoader to restart the app or inspect its logs.</p>"
        "</div>"
    )


def maybe_send_crash_email(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, reason: str
) -> bool:
    """Called exactly when a health check moves an app running -> failed.

    Returns True if an email was sent (or handed to the mailer stub).
    """
    if not config.notifications.crash_emails_enabled:
        return False
    rt = runtime_repo.get(conn, app.id)
    if rt is None or not rt.deployed_healthy:
        return False  # never passed initial health: deployment-domain failure
    if rt.started_at is None:
        return False
    grace = timedelta(seconds=config.health.grace_period_seconds)
    if utc_now() < parse_iso(rt.started_at) + grace:
        log.info("crash within grace period for %s; no email", app.slug)
        return False
    event_key = f"crash:{rt.started_at}:{rt.pid or 0}"
    if notif_repo.was_sent(conn, app.id, event_key):
        return False

    owner = users_repo.get(conn, app.owner_id)
    recipients = [addr for addr in [owner.email, *config.notifications.admin_cc] if addr]
    if not recipients:
        log.warning("crash email for %s skipped: owner has no email address", app.slug)
        return False

    mailer.send_mail(
        subject=f"[WALoader] App '{app.name}' crashed",
        sender=config.notifications.sender,
        recipients=recipients,
        html_body=build_crash_email_html(
            app, reason, last_healthy_at=rt.last_healthy_at
        ),
    )
    notif_repo.mark_sent(conn, app.id, event_key)
    audit_repo.record(
        conn, actor="health-check", action="notify.crash", target=app.slug,
        details={"reason": reason, "recipients": len(recipients)},
    )
    conn.commit()
    log.info("crash email sent for %s to %d recipient(s)", app.slug, len(recipients))
    return True
