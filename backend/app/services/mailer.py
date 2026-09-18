"""Sending mail, and what to do when there is nowhere to send it.

The application has never sent email. This module exists because the password
reset flow needs to, and it is deliberately the smallest thing that can: one
function, `smtplib` from the standard library, no new dependency and no
templating engine for what is currently three plain-text messages.

**An unconfigured mailbox is not an error.** With no `STOCKSYNC_SMTP_HOST` the
message is written to `storage/outbox/` instead. That is the right behaviour in
development — where configuring a relay to click a link on your own machine
would be absurd — and it is loud enough in production that nobody can mistake it
for delivery: the log line says the mail was *not* sent, why, and where the
message went.

**The log is not a delivery mechanism.** It used to be: the whole message was
written to the log, on the theory that in development you would read the link
from your terminal. That never worked, because `RedactingFilter` rewrites
`token=<jwt>` to `token=***` — correctly, since the rule is never to log a
credential, and a reset link is a password-equivalent. The filter was doing its
job and the link came out unusable, so the two requirements are separated here:
the log says a message exists and where, the file holds the link.

**Sending never fails the request that asked for it.** A reset endpoint that
returns 500 because a relay is down tells an unauthenticated caller that the
address exists, which is exactly what the endpoint is written to avoid. Failures
are logged and swallowed; the caller gets the same answer either way.
"""

from __future__ import annotations

import logging
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

#: Everything that is not safe in a filename on every platform we run on.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sender(settings: Settings) -> str:
    return settings.resolved_smtp_from or settings.smtp_username or "no-reply@localhost"


def _build(settings: Settings, *, to: str, subject: str, body: str) -> EmailMessage:
    """One plain-text message, encoded however `set_content` sees fit.

    Which is quoted-printable, or base64 once the body contains a non-ASCII
    character — both correct on the wire and both illegible in a text editor.
    That is the mail format's business; see `_spool` for the copy meant for a
    person.
    """
    message = EmailMessage()
    message["From"] = _sender(settings)
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


def _spool(settings: Settings, *, to: str, subject: str, body: str) -> Path | None:
    """Write an undeliverable message to the outbox. Returns where it landed.

    Deliberately **not** a `.eml`. A serialised message encodes its body —
    quoted-printable soft-wraps a 265-character link across four lines, and one
    em dash anywhere in the text switches the whole thing to base64. Either way
    the link cannot be read, and reading the link is the only reason this file
    exists. So the headers are written as four plain lines and the body exactly
    as it was composed.

    Best effort by design: a read-only or full disk must not turn a mail
    problem into a failed request, so the exception is logged and swallowed
    exactly like an SMTP one.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    recipient = _UNSAFE.sub("_", to or "unknown")[:64]
    path = settings.mail_outbox_dir / f"{stamp}-{recipient}.txt"

    headers = f"From: {_sender(settings)}\nTo: {to}\nSubject: {subject}\n"
    content = f"{headers}\n{body}"

    try:
        settings.mail_outbox_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError:
        log.warning("could not write the message to %s", settings.mail_outbox_dir, exc_info=True)
        return None
    return path


def send(settings: Settings, *, to: str, subject: str, body: str) -> bool:
    """Deliver one plain-text message. Returns whether it actually went.

    The return value is for logs and tests, not for the caller's response: no
    endpoint may vary its answer by whether mail was delivered, because that
    leaks whether the address was worth delivering to.
    """
    message = _build(settings, to=to, subject=subject, body=body)

    if not settings.email_configured:
        spooled = _spool(settings, to=to, subject=subject, body=body)
        log.warning(
            "email not sent (no STOCKSYNC_SMTP_HOST configured) — to=%s subject=%s; "
            "the message with its link is at %s",
            to,
            subject,
            spooled or "(could not be written)",
        )
        return False

    try:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
        ) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        # `exc_info` rather than the message alone: an SMTP failure is usually a
        # configuration problem, and the exception type is most of the diagnosis.
        # The message is spooled as well, so a relay that was down at the wrong
        # moment does not also cost the user their link.
        spooled = _spool(settings, to=to, subject=subject, body=body)
        log.warning(
            "could not send email to %s via %s:%s — the message is at %s",
            to,
            settings.smtp_host,
            settings.smtp_port,
            spooled or "(could not be written)",
            exc_info=True,
        )
        return False

    log.info("sent %r to %s", subject, to)
    return True
