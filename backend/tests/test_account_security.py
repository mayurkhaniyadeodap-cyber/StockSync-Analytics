"""Changing your own email or password while signed in.

Both are authentication changes, so the interesting assertions are again the
refusals: a session alone must not be enough to move the login identity, a
verification link must not survive the change it made, and a password change
must not leave the old password's sessions alive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.db import session as session_module
from app.models import AuthSession, User, normalize_email
from app.services import account
from tests.conftest import TEST_EMAIL, TEST_PASSWORD

CHANGE_EMAIL = "/api/auth/change-email"
VERIFY_EMAIL = "/api/auth/verify-email"
CHANGE_PASSWORD = "/api/auth/change-password"
ME = "/api/auth/me"
LOGIN = "/api/auth/login"

NEW_EMAIL = "moved@deodap.in"
NEW_PASSWORD = "a-long-enough-new-password"


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Everything the service tried to send.

    Captured at `mailer.send` rather than read from the log, because the logging
    filter redacts credential-shaped strings and a signed JWT is exactly that.
    """
    sent: list[dict[str, str]] = []

    def capture(_settings: object, *, to: str, subject: str, body: str) -> bool:
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(account.mailer, "send", capture)
    return sent


def token_from(mailbox: list[dict[str, str]]) -> str:
    links = [
        word
        for message in mailbox
        for word in message["body"].split()
        if "verify-email?token=" in word
    ]
    assert links, "no verification link was sent"
    return links[-1].split("token=", 1)[1]


def account_row() -> User:
    with session_module.get_session_factory()() as db:
        user = db.scalar(select(User).order_by(User.id))
        assert user is not None
        return user


def live_sessions() -> int:
    with session_module.get_session_factory()() as db:
        return len([s for s in db.scalars(select(AuthSession)) if s.revoked_at is None])


def add_colleague(email: str) -> None:
    """A second account in the same workspace, holding an address."""
    with session_module.get_session_factory()() as db:
        owner = db.scalar(select(User).order_by(User.id))
        assert owner is not None
        db.add(
            User(
                workspace_id=owner.workspace_id,
                email=email,
                email_normalized=normalize_email(email),
                password_hash=hash_password("another-fine-password"),
                full_name="A Colleague",
                role="analyst",
                timezone="UTC",
            )
        )
        db.commit()


def ask_to_move(api: TestClient, to: str = NEW_EMAIL, password: str = TEST_PASSWORD):
    return api.post(CHANGE_EMAIL, json={"new_email": to, "current_password": password})


class TestMovingTheEmailNeedsMoreThanASession:
    def test_the_current_password_is_required(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """A borrowed laptop has the cookie. What it does not have is this."""
        response = ask_to_move(signed_in, password="not-it")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "current_password_incorrect"
        assert mailbox == []

    def test_an_unauthenticated_caller_is_refused(self, api: TestClient) -> None:
        assert ask_to_move(api).status_code == 401

    def test_a_malformed_address_is_refused_by_validation(self, signed_in: TestClient) -> None:
        assert ask_to_move(signed_in, to="not-an-address").status_code == 422

    def test_the_address_you_already_have_is_refused(self, signed_in: TestClient) -> None:
        response = ask_to_move(signed_in, to=TEST_EMAIL)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "email_unchanged"


class TestNothingChangesUntilTheLinkIsOpened:
    def test_the_request_writes_nothing(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """Abandoning the flow, or never receiving the mail, leaves the user
        exactly where they were."""
        ask_to_move(signed_in)

        assert account_row().email == TEST_EMAIL

    def test_the_mail_goes_to_the_new_address(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """Sending it to the old one would prove only what the session already
        proved."""
        ask_to_move(signed_in)

        assert [message["to"] for message in mailbox] == [NEW_EMAIL]

    def test_opening_the_link_completes_the_change(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        ask_to_move(signed_in)

        response = signed_in.post(VERIFY_EMAIL, json={"token": token_from(mailbox)})

        assert response.status_code == 200
        assert response.json()["email"] == NEW_EMAIL
        assert account_row().email == NEW_EMAIL

    def test_the_new_address_is_what_signs_in(
        self, api: TestClient, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        ask_to_move(signed_in)
        signed_in.post(VERIFY_EMAIL, json={"token": token_from(mailbox)})

        assert (
            api.post(LOGIN, json={"email": NEW_EMAIL, "password": TEST_PASSWORD}).status_code == 200
        )

    def test_the_link_works_without_a_session(
        self, api: TestClient, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """It is opened wherever the new mailbox is read, which is routinely a
        browser with no session."""
        ask_to_move(signed_in)

        assert api.post(VERIFY_EMAIL, json={"token": token_from(mailbox)}).status_code == 200


class TestTheVerificationLinkIsSingleUse:
    def test_it_cannot_be_opened_twice(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        ask_to_move(signed_in)
        token = token_from(mailbox)
        assert signed_in.post(VERIFY_EMAIL, json={"token": token}).status_code == 200

        again = signed_in.post(VERIFY_EMAIL, json={"token": token})

        assert again.status_code == 400
        assert again.json()["error"]["code"] == "email_token_invalid"

    def test_a_sibling_request_dies_with_it(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """Fire off two, use one, and the other stops working — the token is
        bound to the address it was issued from."""
        ask_to_move(signed_in, to="first@deodap.in")
        first = token_from(mailbox)
        ask_to_move(signed_in)
        second = token_from(mailbox)

        assert signed_in.post(VERIFY_EMAIL, json={"token": second}).status_code == 200
        assert signed_in.post(VERIFY_EMAIL, json={"token": first}).status_code == 400

    def test_an_expired_link_is_refused(self, signed_in: TestClient) -> None:
        settings = signed_in.app.state.settings
        user = account_row()
        expired = jwt.encode(
            {
                "sub": str(user.id),
                "typ": account.TOKEN_TYPE,
                "new": NEW_EMAIL,
                "old": user.email_normalized,
                "exp": datetime.now(UTC) - timedelta(minutes=1),
            },
            settings.resolved_jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        assert signed_in.post(VERIFY_EMAIL, json={"token": expired}).status_code == 400

    def test_a_reset_token_is_not_a_verification_token(self, signed_in: TestClient) -> None:
        """Both are signed with the same secret; `typ` is what separates them."""
        settings = signed_in.app.state.settings
        user = account_row()
        wrong_type = jwt.encode(
            {
                "sub": str(user.id),
                "typ": "password_reset",
                "new": NEW_EMAIL,
                "old": user.email_normalized,
                "exp": datetime.now(UTC) + timedelta(minutes=10),
            },
            settings.resolved_jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        assert signed_in.post(VERIFY_EMAIL, json={"token": wrong_type}).status_code == 400

    def test_rubbish_is_refused(self, signed_in: TestClient) -> None:
        assert signed_in.post(VERIFY_EMAIL, json={"token": "not-a-jwt"}).status_code == 400


class TestDuplicateAddressesAreRefused:
    def test_a_taken_address_is_refused_up_front(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        add_colleague("colleague@deodap.in")

        response = ask_to_move(signed_in, to="colleague@deodap.in")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "email_taken"
        assert mailbox == []

    def test_a_taken_address_is_refused_again_at_confirmation(
        self, signed_in: TestClient, mailbox: list[dict[str, str]]
    ) -> None:
        """Someone else may take it between the request and the click, and the
        unique constraint would otherwise surface as a 500."""
        ask_to_move(signed_in)
        token = token_from(mailbox)

        add_colleague(NEW_EMAIL)

        response = signed_in.post(VERIFY_EMAIL, json={"token": token})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "email_taken"


class TestChangingThePassword:
    def test_the_current_password_is_required(self, signed_in: TestClient) -> None:
        response = signed_in.post(
            CHANGE_PASSWORD,
            json={"current_password": "not-it", "new_password": NEW_PASSWORD},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "current_password_incorrect"

    def test_an_unauthenticated_caller_is_refused(self, api: TestClient) -> None:
        response = api.post(
            CHANGE_PASSWORD,
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )

        assert response.status_code == 401

    def test_a_short_password_is_refused(self, signed_in: TestClient) -> None:
        response = signed_in.post(
            CHANGE_PASSWORD, json={"current_password": TEST_PASSWORD, "new_password": "short"}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "password_too_short"

    def test_reusing_the_same_password_is_refused(self, signed_in: TestClient) -> None:
        response = signed_in.post(
            CHANGE_PASSWORD,
            json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "password_unchanged"

    def test_the_new_password_is_what_signs_in(
        self, api: TestClient, signed_in: TestClient
    ) -> None:
        signed_in.post(
            CHANGE_PASSWORD,
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )

        assert (
            api.post(LOGIN, json={"email": TEST_EMAIL, "password": NEW_PASSWORD}).status_code == 200
        )
        assert (
            api.post(LOGIN, json={"email": TEST_EMAIL, "password": TEST_PASSWORD}).status_code
            == 401
        )

    def test_it_is_stored_hashed_not_in_the_clear(self, signed_in: TestClient) -> None:
        """Through the same argon2id helper every other password goes through."""
        signed_in.post(
            CHANGE_PASSWORD,
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )

        stored = account_row().password_hash
        assert NEW_PASSWORD not in stored
        assert stored.startswith("$argon2id$")

    def test_every_earlier_session_is_revoked(self, signed_in: TestClient) -> None:
        """A password change is usually done *about* someone else's browser
        holding a live refresh token. Sparing it would defeat the point."""
        assert live_sessions() >= 1

        signed_in.post(
            CHANGE_PASSWORD,
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )

        # Exactly one: the replacement this browser was handed on the way out.
        assert live_sessions() == 1

    def test_the_caller_stays_signed_in(self, signed_in: TestClient) -> None:
        signed_in.post(
            CHANGE_PASSWORD,
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )

        assert signed_in.get(ME).status_code == 200


def test_profile_fields_are_untouched_by_either_change(
    signed_in: TestClient, mailbox: list[dict[str, str]]
) -> None:
    """Name, role and time zone are a different concern and stay put."""
    before = account_row()
    name, role, timezone = before.full_name, before.role, before.timezone

    ask_to_move(signed_in)
    signed_in.post(VERIFY_EMAIL, json={"token": token_from(mailbox)})
    signed_in.post(
        CHANGE_PASSWORD, json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD}
    )

    after = account_row()
    assert (after.full_name, after.role, after.timezone) == (name, role, timezone)
