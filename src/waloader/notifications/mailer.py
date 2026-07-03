"""The mail seam.

REPLACE THE BODY OF ``send_mail`` WITH THE CORPORATE MAILER AT WORK.

This shipped implementation is a stub: it logs the subject and recipients
(never the body, which may reference internal apps) and returns normally, so
every surrounding behavior — crash detection, dedupe, cooldown, recipients,
HTML construction — works identically at home and at work. The signature below
is exactly the corporate function's signature; swapping in the real
implementation is a one-function change and nothing else in WALoader moves.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def send_mail(*, subject: str, sender: str, recipients: list[str], html_body: str) -> None:
    """Stub mailer: logs instead of sending. Replace this body at work."""
    log.info(
        "MAIL (stub, not sent): subject=%r sender=%r recipients=%s html_bytes=%d",
        subject, sender, recipients, len(html_body.encode("utf-8")),
    )
