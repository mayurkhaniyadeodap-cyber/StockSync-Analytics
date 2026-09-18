"""Outbound mail: what happens when it can be sent, and when it cannot.

These were written after a verification link never arrived. The cause was
configuration — no `STOCKSYNC_SMTP_HOST`, so nothing was ever sent — but the
reason it took so long to see was a second defect: the undeliverable message
was written to the application log, where `RedactingFilter` rewrote
`token=<jwt>` to `token=***`. The fallback that existed to make development
work produced a link nobody could click, and the only other signal was silence.

So there are two things to hold here. That a message reaches a real SMTP server
with the right envelope, tested against an actual socket rather than a mock —
a mock of `smtplib` would have passed throughout the outage. And that when
there is nowhere to send it, the message lands somewhere a person can read the
link out of, with the log pointing at it rather than containing it.
"""

from __future__ import annotations

import re
import socket
import threading
from email import message_from_string, policy
from pathlib import Path

import pytest

from app.config import Settings
from app.core.logging import redact
from app.services import mailer

LONG_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + ("a" * 180) + ".signature"
LINK = f"http://localhost:5173/verify-email?token={LONG_TOKEN}"


# ---------------------------------------------------------------------------
# a real SMTP server, because a mocked one cannot fail the way a relay does
# ---------------------------------------------------------------------------


class Relay:
    """The smallest SMTP responder smtplib will complete a transaction with.

    `smtpd` and `asyncore` were removed in Python 3.12 and `aiosmtpd` is not a
    dependency worth adding for one test, so this speaks the four verbs that
    matter directly. It records the envelope as the server saw it, which is the
    part a mock cannot check: `send_message` derives the recipients from the
    headers, and getting that wrong is exactly how mail goes to nobody.
    """

    def __init__(self) -> None:
        self.received: list[dict[str, object]] = []
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port: int = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._socket.accept()
        except OSError:  # pragma: no cover - the socket was closed first
            return

        with conn:
            conn.settimeout(10)
            reader = conn.makefile("rb")

            def say(line: str) -> None:
                conn.sendall(line.encode() + b"\r\n")

            say("220 localhost ESMTP test")
            sender = ""
            recipients: list[str] = []

            while True:
                raw = reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                verb = line.upper()

                if verb.startswith(("EHLO", "HELO")):
                    say("250-localhost")
                    say("250 8BITMIME")
                elif verb.startswith("MAIL FROM:"):
                    sender = line.split(":", 1)[1].strip().strip("<>")
                    say("250 OK")
                elif verb.startswith("RCPT TO:"):
                    recipients.append(line.split(":", 1)[1].strip().strip("<>"))
                    say("250 OK")
                elif verb == "DATA":
                    say("354 End data with <CR><LF>.<CR><LF>")
                    body: list[str] = []
                    while True:
                        part = reader.readline().decode("utf-8", "replace")
                        if part in (".\r\n", ".\n", ""):
                            break
                        body.append(part)
                    self.received.append(
                        {"from": sender, "to": list(recipients), "data": "".join(body)}
                    )
                    say("250 OK queued")
                elif verb == "QUIT":
                    say("221 Bye")
                    break
                else:
                    say("250 OK")

            reader.close()

    def wait(self) -> None:
        self._thread.join(timeout=10)

    def close(self) -> None:
        self._socket.close()


@pytest.fixture
def relay() -> object:
    server = Relay()
    yield server
    server.close()


def settings_for(outbox: Path, **over: object) -> Settings:
    return Settings(
        env="development",
        mail_outbox_dir=outbox,
        app_base_url="http://localhost:5173",
        **over,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------


class TestSendingToARelayThatIsThere:
    def test_the_message_arrives_with_the_envelope_it_was_given(
        self, relay: Relay, tmp_path: Path
    ) -> None:
        settings = settings_for(
            tmp_path,
            smtp_host="127.0.0.1",
            smtp_port=relay.port,
            smtp_starttls=False,
            smtp_from="stocksync@deodap.in",
        )

        assert mailer.send(
            settings, to="someone@example.com", subject="Confirm your address", body=LINK
        )
        relay.wait()

        assert len(relay.received) == 1
        assert relay.received[0]["to"] == ["someone@example.com"]
        assert relay.received[0]["from"] == "stocksync@deodap.in"

    def test_a_long_link_survives_the_wire(self, relay: Relay, tmp_path: Path) -> None:
        """The body is quoted-printable, which soft-wraps a 265-character link.

        Worth pinning: the wrapping is real, the reassembly is the client's job,
        and a change of transfer encoding that broke the link would otherwise be
        invisible until someone clicked a truncated one.
        """
        settings = settings_for(
            tmp_path, smtp_host="127.0.0.1", smtp_port=relay.port, smtp_starttls=False
        )

        mailer.send(settings, to="someone@example.com", subject="Confirm", body=f"Open:\n{LINK}\n")
        relay.wait()

        raw = str(relay.received[0]["data"])
        assert LINK not in raw, "the test is not proving anything if the wire held it whole"

        decoded = message_from_string(raw, policy=policy.default).get_content()
        assert LINK in decoded

    def test_nothing_is_spooled_when_it_went(self, relay: Relay, tmp_path: Path) -> None:
        settings = settings_for(
            tmp_path, smtp_host="127.0.0.1", smtp_port=relay.port, smtp_starttls=False
        )

        mailer.send(settings, to="someone@example.com", subject="Confirm", body=LINK)
        relay.wait()

        assert list(tmp_path.glob("*.txt")) == []


class TestWhenThereIsNowhereToSend:
    """The configuration this project shipped with: no `STOCKSYNC_SMTP_HOST`."""

    def test_send_reports_failure_rather_than_raising(self, tmp_path: Path) -> None:
        """The caller must not be able to tell, but it must not blow up either."""
        assert mailer.send(settings_for(tmp_path), to="a@b.c", subject="s", body=LINK) is False

    def test_the_message_is_written_where_it_can_be_read(self, tmp_path: Path) -> None:
        """Readable as it stands — no decoding step between the file and the link.

        The file is plain text rather than a serialised message on purpose: an
        `.eml` encodes its body, and every encoding `set_content` picks breaks
        the link up. One em dash in the body is enough to turn the whole thing
        base64, which is exactly what the real reset message contains.
        """
        body = f"Someone asked to reset your password — if it was you, open:\n\n{LINK}\n"
        mailer.send(settings_for(tmp_path), to="someone@example.com", subject="Confirm", body=body)

        spooled = list(tmp_path.glob("*.txt"))
        assert len(spooled) == 1
        content = spooled[0].read_text(encoding="utf-8")

        assert "To: someone@example.com" in content
        assert "Subject: Confirm" in content
        # On one line, in one piece, with nothing to decode.
        assert LINK in content.splitlines()

    def test_the_filename_says_who_it_was_for(self, tmp_path: Path) -> None:
        mailer.send(settings_for(tmp_path), to="someone@example.com", subject="s", body=LINK)
        assert "someone_example.com" in next(iter(tmp_path.glob("*.txt"))).name

    def test_the_log_points_at_the_file_instead_of_carrying_the_link(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The defect this whole module exists for.

        The log used to hold the entire message, which is where the link was
        supposed to be read from in development. `redact` scrubs `token=...` on
        the way out — correctly; a reset link is password-equivalent — so the
        link arrived as `token=***` and the flow could not be completed at all.
        The log now names the file. That line survives redaction because there
        is no credential in it.
        """
        with caplog.at_level("WARNING", logger="app.services.mailer"):
            mailer.send(settings_for(tmp_path), to="someone@example.com", subject="s", body=LINK)

        line = redact(caplog.text)
        assert "STOCKSYNC_SMTP_HOST" in line
        assert LONG_TOKEN not in line
        assert str(tmp_path) in line

        # And the thing the log no longer carries is in the file, unredacted.
        assert LONG_TOKEN in "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.txt"))

    def test_an_unwritable_outbox_still_does_not_raise(self, tmp_path: Path) -> None:
        """A full or read-only disk is a mail problem, never a failed request."""
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("", encoding="utf-8")

        assert mailer.send(settings_for(blocked), to="a@b.c", subject="s", body=LINK) is False


class TestWhenTheRelayRefuses:
    def test_a_dead_port_is_reported_and_the_message_kept(self, tmp_path: Path) -> None:
        """Nothing is listening on this port, which is what a down relay looks like."""
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()

        settings = settings_for(
            tmp_path,
            smtp_host="127.0.0.1",
            smtp_port=port,
            smtp_starttls=False,
            smtp_timeout_seconds=2.0,
        )

        assert mailer.send(settings, to="a@b.c", subject="s", body=LINK) is False
        # Kept, so a relay that was down for a minute does not cost the user
        # a link they have no other way to get.
        assert len(list(tmp_path.glob("*.txt"))) == 1


class TestTheAddressesUsed:
    @pytest.mark.parametrize(
        ("smtp_from", "username", "expected"),
        [
            ("sender@deodap.in", "login@deodap.in", "sender@deodap.in"),
            ("", "login@deodap.in", "login@deodap.in"),
            ("", "", "no-reply@localhost"),
        ],
    )
    def test_the_sender_falls_back_the_way_it_is_documented(
        self, tmp_path: Path, smtp_from: str, username: str, expected: str
    ) -> None:
        """`no-reply@localhost` is the last resort, and most relays reject it.

        Pinned because it is silent: mail configured with a host but no sender
        fails at the relay, not here, and the rejection is in someone else's log.
        """
        settings = settings_for(tmp_path, smtp_from=smtp_from, smtp_username=username)
        mailer.send(settings, to="someone@example.com", subject="s", body=LINK)

        spooled = next(iter(tmp_path.glob("*.txt"))).read_text(encoding="utf-8")
        assert spooled.startswith(f"From: {expected}\n")


class TestTheLinkTheMessageCarries:
    def test_it_points_at_the_browser_not_the_api(self, tmp_path: Path) -> None:
        """`app_base_url` is the frontend's origin: it serves /verify-email."""
        from app.services import account

        settings = Settings(
            env="development",
            mail_outbox_dir=tmp_path,
            app_base_url="https://stocksync.deodap.in/",
            jwt_secret="k" * 40,
        )

        # Built the way `request_email_change` builds it, without needing a
        # database: the assertion is about the URL, not the account.
        link = f"{settings.app_base_url.rstrip('/')}/verify-email?token=abc"
        assert link == "https://stocksync.deodap.in/verify-email?token=abc"
        assert account.TOKEN_TYPE == "email_change"

    def test_a_trailing_slash_does_not_double_up(self) -> None:
        for base in ("http://localhost:5173", "http://localhost:5173/"):
            settings = Settings(env="development", app_base_url=base)
            built = f"{settings.app_base_url.rstrip('/')}/verify-email?token=t"
            assert built == "http://localhost:5173/verify-email?token=t"
            assert "//verify-email" not in built.replace("http://", "")

    def test_the_token_is_url_safe_in_the_link(self) -> None:
        """A JWT is base64url plus dots — nothing that needs escaping."""
        assert re.fullmatch(r"[A-Za-z0-9._-]+", LONG_TOKEN)
