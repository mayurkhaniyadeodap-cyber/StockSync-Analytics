"""The login flow, end to end against a real database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config as config_module
from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE
from app.core.security import hash_password
from app.db import session as session_module
from app.db.base import Base
from app.main import create_app
from app.models import AuthSession, User, UserPreferences, Workspace

PASSWORD = "a-perfectly-fine-password"


@pytest.fixture
def app_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client backed by a migrated, seeded, throwaway database."""
    monkeypatch.setenv(
        "STOCKSYNC_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'auth.db').as_posix()}"
    )
    monkeypatch.setenv("STOCKSYNC_ENV", "test")
    monkeypatch.setenv("STOCKSYNC_JWT_SECRET", "t" * 48)

    for cached in (
        config_module.get_settings,
        session_module.get_engine,
        session_module.get_session_factory,
    ):
        cached.cache_clear()

    engine = session_module.get_engine()
    Base.metadata.create_all(engine)

    with session_module.get_session_factory()() as db:
        workspace = Workspace(name="Deodap Retail", slug="deodap")
        db.add(workspace)
        db.flush()
        user = User(
            workspace_id=workspace.id,
            # Mixed case on purpose: lookup normalises, display does not.
            email="Admin@Deodap.in",
            email_normalized="admin@deodap.in",
            password_hash=hash_password(PASSWORD),
            full_name="Administrator",
            role="Admin",
        )
        db.add(user)
        db.flush()
        db.add(UserPreferences(user_id=user.id))
        db.commit()

    with TestClient(create_app(config_module.get_settings())) as client:
        yield client

    engine.dispose()
    for cached in (
        config_module.get_settings,
        session_module.get_engine,
        session_module.get_session_factory,
    ):
        cached.cache_clear()


def login(client: TestClient, **overrides: object) -> object:
    payload = {"email": "admin@deodap.in", "password": PASSWORD, "remember_me": True}
    payload.update(overrides)
    return client.post("/api/auth/login", json=payload)


class TestLogin:
    def test_successful_login_returns_the_user_and_sets_cookies(
        self, app_client: TestClient
    ) -> None:
        response = login(app_client)

        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "Admin@Deodap.in"  # original casing preserved
        assert body["full_name"] == "Administrator"
        assert body["initials"] == "AD"
        assert body["role"] == "Admin"
        assert body["workspace"]["name"] == "Deodap Retail"
        assert body["preferences"]["theme"] == "light"

        assert ACCESS_COOKIE in response.cookies
        assert REFRESH_COOKIE in response.cookies

    def test_tokens_never_appear_in_the_response_body(self, app_client: TestClient) -> None:
        """They belong in httpOnly cookies, out of JavaScript's reach."""
        body = login(app_client).text

        assert "token" not in body.lower()

    def test_cookies_are_httponly(self, app_client: TestClient) -> None:
        response = login(app_client)

        # get_list, not items(): items() collapses repeated Set-Cookie headers
        # into one comma-joined string, so attributes bleed between cookies.
        cookies = response.headers.get_list("set-cookie")

        assert len(cookies) == 2
        assert all("httponly" in c.lower() for c in cookies)

    def test_email_is_case_insensitive(self, app_client: TestClient) -> None:
        assert login(app_client, email="ADMIN@DEODAP.IN").status_code == 200

    def test_wrong_password_is_rejected(self, app_client: TestClient) -> None:
        response = login(app_client, password="wrong-password")

        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Incorrect email or password."

    def test_unknown_email_gives_the_same_error(self, app_client: TestClient) -> None:
        """Design doc §6: one banner. Distinguishing the two enumerates users."""
        unknown = login(app_client, email="nobody@deodap.in")
        wrong_password = login(app_client, password="wrong-password")

        assert unknown.status_code == wrong_password.status_code == 401
        assert unknown.json()["error"] == wrong_password.json()["error"]

    def test_malformed_email_is_a_validation_error(self, app_client: TestClient) -> None:
        response = login(app_client, email="not-an-email")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_deactivated_user_cannot_sign_in(self, app_client: TestClient) -> None:
        with session_module.get_session_factory()() as db:
            user = db.scalars(select(User)).one()
            user.is_active = False
            db.commit()

        assert login(app_client).status_code == 401

    def test_remember_me_false_uses_a_session_cookie(self, app_client: TestClient) -> None:
        response = login(app_client, remember_me=False)

        refresh_header = next(
            c for c in response.headers.get_list("set-cookie") if c.startswith(REFRESH_COOKIE)
        )
        assert "max-age" not in refresh_header.lower()


class TestCurrentUser:
    def test_me_requires_authentication(self, app_client: TestClient) -> None:
        response = app_client.get("/api/auth/me")

        assert response.status_code == 401
        error = response.json()["error"]
        assert error["code"] == "not_authenticated"
        assert error["next"]

    def test_me_returns_the_signed_in_user(self, app_client: TestClient) -> None:
        login(app_client)

        response = app_client.get("/api/auth/me")

        assert response.status_code == 200
        assert response.json()["email"] == "Admin@Deodap.in"

    def test_a_forged_cookie_is_rejected(self, app_client: TestClient) -> None:
        app_client.cookies.set(ACCESS_COOKIE, "clearly.not.valid")

        assert app_client.get("/api/auth/me").status_code == 401


class TestLogout:
    def test_logout_clears_cookies_and_blocks_further_requests(
        self, app_client: TestClient
    ) -> None:
        login(app_client)
        assert app_client.get("/api/auth/me").status_code == 200

        assert app_client.post("/api/auth/logout").status_code == 204

        assert app_client.get("/api/auth/me").status_code == 401

    def test_logout_revokes_the_session_row(self, app_client: TestClient) -> None:
        """The access token is still cryptographically valid — the row is what stops it."""
        login(app_client)
        app_client.post("/api/auth/logout")

        with session_module.get_session_factory()() as db:
            auth_session = db.scalars(select(AuthSession)).one()
            assert auth_session.revoked_at is not None

    def test_a_revoked_session_rejects_a_still_valid_access_token(
        self, app_client: TestClient
    ) -> None:
        response = login(app_client)
        access_token = response.cookies[ACCESS_COOKIE]
        app_client.post("/api/auth/logout")

        # Re-present the token the browser was told to forget.
        app_client.cookies.set(ACCESS_COOKIE, access_token)

        assert app_client.get("/api/auth/me").status_code == 401

    def test_logout_without_a_session_is_not_an_error(self, app_client: TestClient) -> None:
        assert app_client.post("/api/auth/logout").status_code == 204


class TestRefresh:
    def test_refresh_issues_a_new_session(self, app_client: TestClient) -> None:
        login(app_client)
        original = app_client.cookies[REFRESH_COOKIE]

        response = app_client.post("/api/auth/refresh")

        assert response.status_code == 200
        assert response.json()["email"] == "Admin@Deodap.in"
        assert app_client.cookies[REFRESH_COOKIE] != original

    def test_the_old_refresh_token_stops_working(self, app_client: TestClient) -> None:
        """Rotation makes a stolen refresh token single-use."""
        login(app_client)
        original = app_client.cookies[REFRESH_COOKIE]
        app_client.post("/api/auth/refresh")

        app_client.cookies.set(REFRESH_COOKIE, original)
        response = app_client.post("/api/auth/refresh")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "session_expired"

    def test_refresh_without_a_cookie_is_rejected(self, app_client: TestClient) -> None:
        assert app_client.post("/api/auth/refresh").status_code == 401

    def test_an_expired_refresh_token_is_rejected(self, app_client: TestClient) -> None:
        login(app_client)

        with session_module.get_session_factory()() as db:
            auth_session = db.scalars(select(AuthSession)).one()
            auth_session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()

        assert app_client.post("/api/auth/refresh").status_code == 401


class TestTheClientIsToldWhenTheTokenDies:
    """The access cookie lives fifteen minutes and the page cannot read it.

    Without the expiry the client had to either guess `access_token_minutes` or
    wait for a request to fail. It waited: nothing called `/auth/refresh`, so a
    tab open past the quarter hour got "You're not signed in." on its next
    request with a thirty-day refresh cookie sitting unused in the jar.
    """

    def expiry(self, response: object) -> datetime:
        raw = response.json()["access_expires_at"]  # type: ignore[attr-defined]
        assert raw is not None
        return datetime.fromisoformat(raw)

    def test_login_says_when_the_token_expires(self, app_client: TestClient) -> None:
        before = datetime.now(UTC)

        expires = self.expiry(login(app_client))

        # The window the client schedules against, not a hard-coded fifteen.
        assert timedelta(minutes=14) < expires - before < timedelta(minutes=16)

    def test_me_says_it_too_so_a_reload_can_reschedule(self, app_client: TestClient) -> None:
        """A fresh tab never sees the login response; it only calls `/auth/me`."""
        login(app_client)

        assert self.expiry(app_client.get("/api/auth/me")) > datetime.now(UTC)

    def test_refresh_says_it_so_the_next_renewal_can_be_scheduled(
        self, app_client: TestClient
    ) -> None:
        login(app_client)
        first = self.expiry(app_client.get("/api/auth/me"))

        assert self.expiry(app_client.post("/api/auth/refresh")) >= first

    def test_the_profile_patches_send_null_rather_than_a_stale_value(
        self, app_client: TestClient
    ) -> None:
        """They neither issue a token nor read one, and a client that took their
        null as an answer would throw away a schedule that is still good."""
        login(app_client)

        for response in (
            app_client.patch("/api/me", json={"full_name": "Administrator"}),
            app_client.patch("/api/me/preferences", json={"theme": "dark"}),
        ):
            assert response.status_code == 200
            assert response.json()["access_expires_at"] is None

    def test_the_expiry_is_a_timestamp_and_never_the_token(self, app_client: TestClient) -> None:
        """Telling the client *when* must not become telling it *what*."""
        response = login(app_client)
        body = response.text

        assert response.cookies[ACCESS_COOKIE] not in body
        assert response.cookies[REFRESH_COOKIE] not in body


class TestAnExpiredAccessTokenIsRecoverable:
    """The 401 the client now retries, and the refresh that answers it.

    This is the whole reported bug: an access token dies long before the session
    does, and every protected request fails until something rotates it.
    """

    def expire_the_access_cookie(self, client: TestClient) -> None:
        """What the browser does fifteen minutes in: the cookie's max-age
        elapses and it is simply no longer sent. The refresh cookie remains."""
        client.cookies.delete(ACCESS_COOKIE)

    def test_a_protected_request_fails_once_the_access_token_is_gone(
        self, app_client: TestClient
    ) -> None:
        login(app_client)
        self.expire_the_access_cookie(app_client)

        response = app_client.get("/api/auth/me")

        assert response.status_code == 401
        # The exact sentence the user reported seeing.
        assert response.json()["error"]["message"] == "You're not signed in."

    def test_refresh_recovers_it_without_the_access_cookie(self, app_client: TestClient) -> None:
        """The refresh cookie alone is enough — which is why the client can fix
        a 401 by itself instead of sending the user back to the login form."""
        login(app_client)
        self.expire_the_access_cookie(app_client)

        assert app_client.post("/api/auth/refresh").status_code == 200
        assert app_client.get("/api/auth/me").status_code == 200

    def test_the_replayed_request_succeeds(self, app_client: TestClient) -> None:
        """The sequence the client performs: 401, refresh, same request again."""
        login(app_client)
        self.expire_the_access_cookie(app_client)

        assert app_client.get("/api/analytics/kpis").status_code == 401
        assert app_client.post("/api/auth/refresh").status_code == 200
        assert app_client.get("/api/analytics/kpis").status_code == 200

    def test_a_revoked_session_is_not_recoverable(self, app_client: TestClient) -> None:
        """Signing out has to stay final. Renewal must not resurrect it."""
        login(app_client)
        app_client.post("/api/auth/logout")

        assert app_client.post("/api/auth/refresh").status_code == 401

    def test_an_expired_refresh_row_is_not_recoverable(self, app_client: TestClient) -> None:
        """When the session really is over, refresh says so — which is what
        makes the client redirect to login instead of retrying forever."""
        login(app_client)
        self.expire_the_access_cookie(app_client)

        with session_module.get_session_factory()() as db:
            auth_session = db.scalars(select(AuthSession)).one()
            auth_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()

        response = app_client.post("/api/auth/refresh")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "session_expired"


class TestPreferences:
    def test_updating_one_field_leaves_the_others_alone(self, app_client: TestClient) -> None:
        login(app_client)

        response = app_client.patch("/api/me/preferences", json={"theme": "dark"})

        assert response.status_code == 200
        preferences = response.json()["preferences"]
        assert preferences["theme"] == "dark"
        assert preferences["table_density"] == "comfortable"

    def test_density_persists(self, app_client: TestClient) -> None:
        """Design doc §15: density is persisted per user."""
        login(app_client)
        app_client.patch("/api/me/preferences", json={"table_density": "compact"})

        assert app_client.get("/api/auth/me").json()["preferences"]["table_density"] == "compact"

    def test_an_invalid_value_is_rejected(self, app_client: TestClient) -> None:
        login(app_client)

        response = app_client.patch("/api/me/preferences", json={"theme": "neon"})

        assert response.status_code == 422

    def test_preferences_require_authentication(self, app_client: TestClient) -> None:
        assert app_client.patch("/api/me/preferences", json={"theme": "dark"}).status_code == 401


class TestLowStockThreshold:
    """Settings -> Display. Workspace-scoped, unlike the switches beside it."""

    def test_it_saves_and_is_read_back(self, app_client: TestClient) -> None:
        login(app_client)

        response = app_client.patch("/api/me/preferences", json={"low_stock_threshold": 25})

        assert response.status_code == 200
        assert response.json()["workspace"]["low_stock_threshold"] == 25
        assert app_client.get("/api/auth/me").json()["workspace"]["low_stock_threshold"] == 25

    def test_it_changes_what_counts_as_low_stock(self, app_client: TestClient) -> None:
        """
        The point of the setting: it is the line the Low stock figure is drawn
        at, so analytics has to report the new one rather than the default.
        """
        login(app_client)
        app_client.patch("/api/me/preferences", json={"low_stock_threshold": 25})

        kpis = app_client.get("/api/analytics/kpis").json()

        assert kpis["low_stock_threshold"] == 25

    def test_zero_is_allowed(self, app_client: TestClient) -> None:
        """A workspace that never wants a low-stock warning can say so."""
        login(app_client)

        response = app_client.patch("/api/me/preferences", json={"low_stock_threshold": 0})

        assert response.status_code == 200
        assert response.json()["workspace"]["low_stock_threshold"] == 0

    def test_a_negative_threshold_is_refused(self, app_client: TestClient) -> None:
        login(app_client)

        assert (
            app_client.patch("/api/me/preferences", json={"low_stock_threshold": -1}).status_code
            == 422
        )

    def test_it_does_not_disturb_the_display_switches(self, app_client: TestClient) -> None:
        login(app_client)
        app_client.patch("/api/me/preferences", json={"theme": "dark"})

        body = app_client.patch("/api/me/preferences", json={"low_stock_threshold": 5}).json()

        assert body["preferences"]["theme"] == "dark"
        assert body["workspace"]["low_stock_threshold"] == 5


class TestProfile:
    """Settings -> Profile."""

    def test_the_name_saves(self, app_client: TestClient) -> None:
        login(app_client)

        response = app_client.patch("/api/me", json={"full_name": "Priya Mehta"})

        assert response.status_code == 200
        assert response.json()["full_name"] == "Priya Mehta"
        assert app_client.get("/api/auth/me").json()["full_name"] == "Priya Mehta"

    def test_the_initials_follow_the_name(self, app_client: TestClient) -> None:
        """The header avatar is derived, so a rename has to reach it."""
        login(app_client)

        assert (
            app_client.patch("/api/me", json={"full_name": "Priya Mehta"}).json()["initials"]
            == "PM"
        )

    def test_the_time_zone_saves(self, app_client: TestClient) -> None:
        login(app_client)

        response = app_client.patch("/api/me", json={"timezone": "Asia/Dubai"})

        assert response.status_code == 200
        assert response.json()["timezone"] == "Asia/Dubai"

    def test_one_field_at_a_time_leaves_the_other_alone(self, app_client: TestClient) -> None:
        login(app_client)
        app_client.patch("/api/me", json={"timezone": "Asia/Dubai"})

        body = app_client.patch("/api/me", json={"full_name": "Priya Mehta"}).json()

        assert body["timezone"] == "Asia/Dubai"
        assert body["full_name"] == "Priya Mehta"

    def test_the_email_and_role_are_not_writable_here(self, app_client: TestClient) -> None:
        """
        Email is the login identity and role is set by the workspace admin, so
        neither is a profile edit. Both are shown; an attempt to send them is
        ignored rather than silently applied.
        """
        login(app_client)
        before = app_client.get("/api/auth/me").json()

        body = app_client.patch(
            "/api/me",
            json={"email": "someone-else@deodap.in", "role": "Owner"},
        ).json()

        assert body["email"] == before["email"]
        assert body["role"] == before["role"]

    def test_a_blank_name_is_refused(self, app_client: TestClient) -> None:
        login(app_client)

        assert app_client.patch("/api/me", json={"full_name": ""}).status_code == 422

    def test_surrounding_space_is_trimmed(self, app_client: TestClient) -> None:
        login(app_client)

        body = app_client.patch("/api/me", json={"full_name": "  Priya Mehta  "}).json()

        assert body["full_name"] == "Priya Mehta"

    def test_it_requires_authentication(self, app_client: TestClient) -> None:
        assert app_client.patch("/api/me", json={"full_name": "x"}).status_code == 401

    def test_the_password_hash_is_never_returned(self, app_client: TestClient) -> None:
        login(app_client)

        body = app_client.patch("/api/me", json={"full_name": "Priya Mehta"}).text

        assert "password" not in body.lower()
