"""Forgotten-password reset, end to end through the real endpoints.

Most of what is asserted here is *refusal*: this is the only unauthenticated
endpoint in the application that can change a password, so the interesting cases
are the ones where it must not. Token reuse, a token for another kind of claim,
an expired one, an address that does not exist — each has its own way of going
wrong quietly, and each is pinned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import throttle as throttle_module
from app.db import session as session_module
from app.models import AuthSession, User
from app.services import password_reset

FORGOT = "/api/auth/forgot-password"
RESET = "/api/auth/reset-password"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"

NEW_PASSWORD = "a-long-enough-new-password"


@pytest.fixture(autouse=True)
def _clear_throttle() -> None:
    """These endpoints share the login counters, so tests must not inherit
    each other's attempts."""
    throttle_module.reset_login_throttle()


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Everything the service tried to send.

    Captured at `mailer.send` rather than read out of the log. The logging
    filter redacts credential-shaped strings on every record, and a signed JWT
    is exactly that shape — so the log says `***` where the token was, which is
    the filter working correctly and useless to a test that needs the token.
    """
    sent: list[dict[str, str]] = []

    def capture(_settings: object, *, to: str, subject: str, body: str) -> bool:
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(password_reset.mailer, "send", capture)
    return sent


def sent_links(mailbox: list[dict[str, str]]) -> list[str]:
    return [
        word
        for message in mailbox
        for word in message["body"].split()
        if "reset-password?token=" in word
    ]


def account(api: TestClient) -> User:
    with session_module.get_session_factory()() as db:
        user = db.scalar(select(User))
        assert user is not None
        return user


def request_reset(api: TestClient, mailbox: list[dict[str, str]], email: str) -> str:
    response = api.post(FORGOT, json={"email": email})
    assert response.status_code == 200
    links = sent_links(mailbox)
    assert links, "no reset link was sent"
    return links[-1].split("token=", 1)[1]


class TestTheRequestTellsYouNothing:
    def test_a_real_address_gets_the_standard_answer(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        response = signed_in.post(FORGOT, json={"email": account(signed_in).email})

        assert response.status_code == 200
        assert "If that address has an account" in response.json()["detail"]

    def test_an_unknown_address_gets_exactly_the_same_answer(self, signed_in: TestClient) -> None:
        """The reason this endpoint returns a fixed string: anything that varied
        would be a free account-enumeration oracle on a login page."""
        known = signed_in.post(FORGOT, json={"email": account(signed_in).email})
        unknown = signed_in.post(FORGOT, json={"email": "nobody@deodap.in"})

        assert unknown.status_code == known.status_code
        assert unknown.json() == known.json()

    def test_an_unknown_address_sends_no_mail(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        signed_in.post(FORGOT, json={"email": "nobody@deodap.in"})

        assert sent_links(mailbox) == []

    def test_a_malformed_address_is_refused_by_validation(self, signed_in: TestClient) -> None:
        assert signed_in.post(FORGOT, json={"email": "not-an-address"}).status_code == 422


class TestTheTokenIsSingleUse:
    def test_a_fresh_token_sets_the_password(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        token = request_reset(signed_in, mailbox, account(signed_in).email)

        response = signed_in.post(RESET, json={"token": token, "password": NEW_PASSWORD})

        assert response.status_code == 200
        assert response.json()["email"] == account(signed_in).email

    def test_the_new_password_actually_works(
        self, api: TestClient, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        email = account(signed_in).email
        token = request_reset(signed_in, mailbox, email)
        signed_in.post(RESET, json={"token": token, "password": NEW_PASSWORD})

        assert api.post(LOGIN, json={"email": email, "password": NEW_PASSWORD}).status_code == 200

    def test_the_same_token_cannot_be_used_twice(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """Not by a used-flag but by the fingerprint: the reset wrote a new
        hash, so the token no longer describes the account."""
        token = request_reset(signed_in, mailbox, account(signed_in).email)
        assert (
            signed_in.post(RESET, json={"token": token, "password": NEW_PASSWORD}).status_code
            == 200
        )

        again = signed_in.post(RESET, json={"token": token, "password": "another-long-password"})

        assert again.status_code == 400
        assert again.json()["error"]["code"] == "reset_token_invalid"

    def test_an_older_outstanding_token_dies_with_it(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """Three links requested, one used — the other two stop working, which
        is what a reader expects "single use" to mean. A used-flag column would
        have left them live."""
        email = account(signed_in).email
        first = request_reset(signed_in, mailbox, email)
        second = request_reset(signed_in, mailbox, email)
        assert first != second

        assert (
            signed_in.post(RESET, json={"token": second, "password": NEW_PASSWORD}).status_code
            == 200
        )

        stale = signed_in.post(RESET, json={"token": first, "password": "yet-another-password"})
        assert stale.status_code == 400


class TestTheTokenIsRefusedWhenItShouldBe:
    def test_an_expired_token_is_refused(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        settings = signed_in.app.state.settings
        user = account(signed_in)
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "typ": password_reset.TOKEN_TYPE,
                "fp": password_reset._fingerprint(user.password_hash),
                "exp": datetime.now(UTC) - timedelta(minutes=1),
            },
            settings.resolved_jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        response = signed_in.post(RESET, json={"token": expired, "password": NEW_PASSWORD})

        assert response.status_code == 400

    def test_an_access_token_is_not_a_reset_token(self, signed_in: TestClient) -> None:
        """Both are signed with the same secret. `typ` is what stops one being
        presented to the other's endpoint."""
        settings = signed_in.app.state.settings
        user = account(signed_in)
        wrong_type = jwt.encode(
            {
                "sub": str(user.id),
                "typ": "access",
                "fp": password_reset._fingerprint(user.password_hash),
                "exp": datetime.now(UTC) + timedelta(minutes=10),
            },
            settings.resolved_jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        response = signed_in.post(RESET, json={"token": wrong_type, "password": NEW_PASSWORD})

        assert response.status_code == 400

    def test_a_token_signed_with_another_key_is_refused(self, signed_in: TestClient) -> None:
        user = account(signed_in)
        forged = jwt.encode(
            {
                "sub": str(user.id),
                "typ": password_reset.TOKEN_TYPE,
                "fp": password_reset._fingerprint(user.password_hash),
                "exp": datetime.now(UTC) + timedelta(minutes=10),
            },
            "not-the-servers-secret",
            algorithm="HS256",
        )

        assert (
            signed_in.post(RESET, json={"token": forged, "password": NEW_PASSWORD}).status_code
            == 400
        )

    def test_rubbish_is_refused_without_a_stack_trace(self, signed_in: TestClient) -> None:
        response = signed_in.post(RESET, json={"token": "not-a-jwt", "password": NEW_PASSWORD})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "reset_token_invalid"

    def test_every_refusal_reads_the_same(self, signed_in: TestClient) -> None:
        """Telling a holder of a bad token *which* part was wrong would say
        which part to attack, and none of them changes what the user does."""
        forged = signed_in.post(RESET, json={"token": "not-a-jwt", "password": NEW_PASSWORD})
        empty = signed_in.post(RESET, json={"token": "x.y.z", "password": NEW_PASSWORD})

        assert forged.json()["error"] == empty.json()["error"]


class TestTheNewPasswordIsChecked:
    def test_a_short_password_is_refused(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        token = request_reset(signed_in, mailbox, account(signed_in).email)

        response = signed_in.post(RESET, json={"token": token, "password": "short"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "password_too_short"

    def test_a_refused_password_leaves_the_token_live(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """The length check runs before the token is spent, so a typo does not
        cost the user their link."""
        token = request_reset(signed_in, mailbox, account(signed_in).email)
        signed_in.post(RESET, json={"token": token, "password": "short"})

        assert (
            signed_in.post(RESET, json={"token": token, "password": NEW_PASSWORD}).status_code
            == 200
        )


class TestAResetEndsEverySession:
    def test_existing_sessions_are_revoked(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """A reset is what someone does when they suspect the account is not
        only theirs. Leaving the refresh tokens alive would keep whoever
        prompted it signed in."""
        assert signed_in.get(ME).status_code == 200

        token = request_reset(signed_in, mailbox, account(signed_in).email)
        signed_in.post(RESET, json={"token": token, "password": NEW_PASSWORD})

        with session_module.get_session_factory()() as db:
            live = [s for s in db.scalars(select(AuthSession)) if s.revoked_at is None]

        # Exactly one: the session the reset itself issued.
        assert len(live) == 1

    def test_the_caller_is_signed_in_afterwards(
        self, api: TestClient, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """They have just proved they hold the mailbox and chosen a password;
        asking them to type it again immediately is ceremony."""
        token = request_reset(signed_in, mailbox, account(signed_in).email)
        api.post(RESET, json={"token": token, "password": NEW_PASSWORD})

        assert api.get(ME).status_code == 200


def test_the_link_points_at_the_configured_front_end(
    signed_in: TestClient, mailbox: list[dict[str, str]]
) -> None:
    """Configured rather than taken from the Host header, which an attacker
    could otherwise poison into mailing users a link to their own site."""
    request_reset(signed_in, mailbox, account(signed_in).email)

    base = signed_in.app.state.settings.app_base_url.rstrip("/")
    assert sent_links(mailbox)[-1].startswith(f"{base}/reset-password?token=")
