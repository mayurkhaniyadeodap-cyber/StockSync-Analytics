"""Shopify connection: credential validation, storage and revocation.

Shopify itself is stubbed at ``httpx.get`` — the one seam where this code talks
to the outside world. Nothing here reaches the network, so the suite is
deterministic and needs no store.
"""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.core import crypto
from app.db import session as session_module
from app.models import ShopifyConnection
from app.services import shopify as shopify_service

CONNECTION = "/api/shopify/connection"
SYNC = "/api/shopify/sync"
TOKEN = "shpat_TESTFIXTURENOTAREALTOKEN0000"
GOOD = {"shop_url": "mystore.myshopify.com", "access_token": TOKEN}

SHOP_BODY = {
    "shop": {
        "name": "Deodap Retail",
        "plan_display_name": "Shopify Plus",
        "currency": "INR",
    }
}
SCOPES_BODY = {"access_scopes": [{"handle": "read_products"}, {"handle": "read_orders"}]}


class FakeResponse:
    def __init__(
        self, status_code: int, payload: Any = None, headers: dict[str, str] | None = None
    ):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


def install(monkeypatch: pytest.MonkeyPatch, handler) -> list[str]:
    """Replace httpx.get and record the paths requested."""
    seen: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        seen.append(url)
        return handler(url, **kwargs)

    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def happy(url: str, **_: Any) -> FakeResponse:
    if "access_scopes" in url:
        return FakeResponse(200, SCOPES_BODY)
    return FakeResponse(200, SHOP_BODY)


def fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def default_for(attribute: str) -> Any:
    """A setting's declared default, read off the field.

    Asserting the literal ("2025-01") would make every one of these tests fail
    the day the pinned API version is bumped, which says nothing about the
    behaviour under test — that an unread name falls back to whatever the
    default happens to be.
    """
    return Settings.model_fields[attribute].get_default()


def stored() -> ShopifyConnection | None:
    with session_module.get_session_factory()() as db:
        return db.scalars(select(ShopifyConnection)).first()


class TestDomainNormalisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "mystore.myshopify.com",
            "https://mystore.myshopify.com",
            "http://mystore.myshopify.com/admin",
            "  MyStore.MyShopify.com  ",
            "mystore",  # a bare store name is unambiguous
            # What the browser bar actually shows while you are in the admin —
            # the myshopify host is never displayed there.
            "https://admin.shopify.com/store/mystore/",
            "https://admin.shopify.com/store/mystore",
            "admin.shopify.com/store/mystore/products?selectedView=all",
        ],
    )
    def test_accepts_what_people_paste(self, raw: str) -> None:
        assert shopify_service.normalize_shop_domain(raw) == "mystore.myshopify.com"

    def test_the_admin_host_alone_is_still_rejected(self) -> None:
        """It names no store, so there is nothing to connect to."""
        with pytest.raises(shopify_service.InvalidShopDomainError):
            shopify_service.normalize_shop_domain("https://admin.shopify.com/")

    @pytest.mark.parametrize("raw", ["", "   ", "shop.example.com", "not a domain", "https://"])
    def test_rejects_non_myshopify_addresses(self, raw: str) -> None:
        """The Admin API is only addressable at the myshopify host."""
        with pytest.raises(shopify_service.InvalidShopDomainError):
            shopify_service.normalize_shop_domain(raw)


class TestAuthentication:
    def test_every_endpoint_requires_a_session(self, api: TestClient) -> None:
        assert api.get(CONNECTION).status_code == 401
        assert api.post(CONNECTION, json=GOOD).status_code == 401
        assert api.post(f"{CONNECTION}/test", json=GOOD).status_code == 401
        assert api.delete(CONNECTION).status_code == 401


class TestEmptyState:
    def test_no_connection_yet(self, signed_in: TestClient) -> None:
        body = signed_in.get(CONNECTION).json()

        assert body == {"connected": False, "connection": None, "source": "none"}


class TestTestConnection:
    def test_a_valid_credential_returns_the_store_profile(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, happy)

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["profile"]["store_name"] == "Deodap Retail"
        assert body["profile"]["plan_name"] == "Shopify Plus"
        assert body["profile"]["scopes"] == ["read_products", "read_orders"]

    def test_testing_writes_nothing(self, signed_in: TestClient, monkeypatch) -> None:
        """§9.2 step 3 proves the credential before anything is committed."""
        install(monkeypatch, happy)

        signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert stored() is None

    def test_a_rejected_token_is_a_clear_failure(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, lambda url, **_: FakeResponse(401))

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "shopify_auth_failed"
        assert error["next"]

    def test_an_unknown_store_is_distinguished_from_a_bad_token(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, lambda url, **_: FakeResponse(404))

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 400
        assert "store" in response.json()["error"]["message"].lower()

    def test_a_token_missing_scopes_is_refused(self, signed_in: TestClient, monkeypatch) -> None:
        def only_products(url: str, **_: Any) -> FakeResponse:
            if "access_scopes" in url:
                return FakeResponse(200, {"access_scopes": [{"handle": "read_products"}]})
            return FakeResponse(200, SHOP_BODY)

        install(monkeypatch, only_products)

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "shopify_missing_scopes"
        assert error["detail"]["missing_scopes"] == ["read_orders"]

    def test_a_token_that_cannot_read_its_own_scopes_still_connects(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        """shop.json already proved authentication; unknown scopes are not fatal."""

        def no_scope_endpoint(url: str, **_: Any) -> FakeResponse:
            if "access_scopes" in url:
                return FakeResponse(403)
            return FakeResponse(200, SHOP_BODY)

        install(monkeypatch, no_scope_endpoint)

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 200
        assert response.json()["profile"]["scopes"] == []

    def test_an_unreachable_store_is_a_502(self, signed_in: TestClient, monkeypatch) -> None:
        def boom(url: str, **_: Any) -> FakeResponse:
            raise httpx.ConnectError("dns failure")

        install(monkeypatch, boom)

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "shopify_unreachable"

    def test_rate_limiting_reports_when_to_retry(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, lambda url, **_: FakeResponse(429, headers={"Retry-After": "42"}))

        response = signed_in.post(f"{CONNECTION}/test", json=GOOD)

        assert response.status_code == 429
        error = response.json()["error"]
        assert error["code"] == "shopify_rate_limited"
        assert error["detail"]["retry_after_seconds"] == 42

    def test_a_malformed_url_never_reaches_shopify(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        seen = install(monkeypatch, happy)

        response = signed_in.post(
            f"{CONNECTION}/test", json={"shop_url": "shop.example.com", "access_token": TOKEN}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_shop_domain"
        assert seen == []


class TestConnectionSettings:
    """Settings -> Shopify: the order lookback window."""

    def test_changing_the_window_needs_no_token(self, signed_in: TestClient, monkeypatch) -> None:
        """
        The token is never returned by the API and is not recoverable, so
        requiring it to change an unrelated setting would mean re-issuing a
        Shopify credential to move a dropdown.
        """
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        response = signed_in.patch(CONNECTION, json={"order_lookback_days": 30})

        assert response.status_code == 200, response.json()
        assert response.json()["connection"]["order_lookback_days"] == 30

    def test_the_change_is_stored(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        signed_in.patch(CONNECTION, json={"order_lookback_days": 60})

        connection = stored()
        assert connection is not None
        assert connection.order_lookback_days == 60
        assert signed_in.get(CONNECTION).json()["connection"]["order_lookback_days"] == 60

    def test_it_leaves_the_credential_alone(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)
        before = stored()
        assert before is not None
        token_before = before.access_token_encrypted

        signed_in.patch(CONNECTION, json={"order_lookback_days": 30})

        after = stored()
        assert after is not None
        assert after.access_token_encrypted == token_before
        assert after.status == "connected"

    @pytest.mark.parametrize("days", [0, 1, 45, 120, -30])
    def test_an_unoffered_window_is_refused(
        self, signed_in: TestClient, monkeypatch, days: int
    ) -> None:
        """Each window is a deliberate trade, not a free-form number."""
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        response = signed_in.patch(CONNECTION, json={"order_lookback_days": days})

        assert response.status_code == 422
        assert stored() is not None
        assert stored().order_lookback_days == 90  # type: ignore[union-attr]

    def test_with_no_connection_it_says_so(self, signed_in: TestClient) -> None:
        response = signed_in.patch(CONNECTION, json={"order_lookback_days": 30})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "connection_not_stored"

    def test_an_env_store_reports_that_it_is_not_editable_here(
        self, env_shopify: TestClient
    ) -> None:
        """
        A store named in .env has no row to write to. Answering "not connected"
        would contradict the page, which shows it as connected.
        """
        response = env_shopify.patch(CONNECTION, json={"order_lookback_days": 30})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "env_connection_read_only"

    def test_it_requires_a_session(self, api: TestClient) -> None:
        assert api.patch(CONNECTION, json={"order_lookback_days": 30}).status_code == 401


class TestSaveConnection:
    def test_saving_stores_the_connection(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)

        response = signed_in.post(CONNECTION, json=GOOD)

        assert response.status_code == 200
        body = response.json()
        assert body["connected"] is True
        assert body["connection"]["shop_domain"] == "mystore.myshopify.com"
        assert body["connection"]["store_name"] == "Deodap Retail"
        assert body["connection"]["status"] == "connected"

    def test_the_token_is_encrypted_at_rest(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)

        signed_in.post(CONNECTION, json=GOOD)

        connection = stored()
        assert connection is not None
        assert TOKEN not in connection.access_token_encrypted
        assert connection.access_token_encrypted != TOKEN

    def test_the_stored_token_round_trips(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        connection = stored()
        assert connection is not None
        settings = signed_in.app.state.settings
        assert crypto.decrypt(settings, connection.access_token_encrypted) == TOKEN

    def test_the_connection_carries_the_stored_freshness(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        """
        The newest order Shopify reported, on the connection the whole app reads.

        The dashboard has to be able to say "new orders are waiting" on a page
        load, and the only alternative source is /shopify/freshness, which costs
        a live Shopify request. Every sync already records this, so exposing the
        stored value here makes that answer free -- and, importantly, does not
        require a second endpoint that would return the same rows.
        """
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        latest = datetime(2026, 7, 31, 6, 0, tzinfo=UTC)
        with session_module.get_session_factory()() as db:
            connection = db.scalars(select(ShopifyConnection)).one()
            connection.store_latest_order_at = latest
            connection.freshness_checked_at = latest
            db.commit()

        body = signed_in.get(CONNECTION).json()["connection"]

        assert body["store_latest_order_at"].startswith("2026-07-31T06:00:00")
        assert body["freshness_checked_at"].startswith("2026-07-31T06:00:00")

    def test_a_connection_never_checked_reports_no_freshness(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        """Null, not an invented timestamp: nothing has been read from the store."""
        install(monkeypatch, happy)

        body = signed_in.post(CONNECTION, json=GOOD).json()["connection"]

        assert body["store_latest_order_at"] is None
        assert body["freshness_checked_at"] is None

    def test_the_token_is_never_returned_by_the_api(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        body = signed_in.get(CONNECTION).text

        assert TOKEN not in body
        assert "access_token" not in body

    def test_an_invalid_credential_is_not_stored(self, signed_in: TestClient, monkeypatch) -> None:
        """Validated before storage, so a bad token never lands in the database."""
        install(monkeypatch, lambda url, **_: FakeResponse(401))

        response = signed_in.post(CONNECTION, json=GOOD)

        assert response.status_code == 400
        assert stored() is None

    def test_reconnecting_replaces_the_credential(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)
        first = stored()
        assert first is not None
        original_id = first.id

        signed_in.post(
            CONNECTION,
            json={"shop_url": "other.myshopify.com", "access_token": "shpat_second_token_value"},
        )

        connection = stored()
        assert connection is not None
        # One store per workspace: updated in place, not a second row.
        assert connection.id == original_id
        assert connection.shop_domain == "other.myshopify.com"


class TestVerify:
    def test_verifying_a_stored_credential(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        response = signed_in.post(f"{CONNECTION}/verify")

        assert response.status_code == 200
        assert response.json()["connected"] is True

    def test_a_revoked_token_marks_the_connection_expired(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        """The stored status is what puts the red dot in the sidebar (§4)."""
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        install(monkeypatch, lambda url, **_: FakeResponse(401))
        response = signed_in.post(f"{CONNECTION}/verify")

        assert response.status_code == 400
        connection = stored()
        assert connection is not None
        assert connection.status == "token_expired"

    def test_verifying_without_a_connection_is_404(self, signed_in: TestClient) -> None:
        response = signed_in.post(f"{CONNECTION}/verify")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "shopify_not_connected"


class TestDisconnect:
    def test_disconnecting_clears_the_token(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        response = signed_in.delete(CONNECTION)

        assert response.status_code == 200
        assert response.json()["connected"] is False

        connection = stored()
        assert connection is not None
        assert connection.status == "disconnected"
        # A disconnected store holding a usable token is a credential nobody
        # is watching.
        assert connection.access_token_encrypted == ""
        assert connection.disconnected_at is not None

    def test_the_record_of_what_was_connected_survives(
        self, signed_in: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)
        signed_in.delete(CONNECTION)

        connection = stored()
        assert connection is not None
        assert connection.shop_domain == "mystore.myshopify.com"
        assert connection.connected_at is not None

    def test_disconnecting_twice_is_a_clean_404(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)
        signed_in.delete(CONNECTION)

        response = signed_in.delete(CONNECTION)

        assert response.status_code == 404

    def test_disconnecting_without_a_connection_is_404(self, signed_in: TestClient) -> None:
        assert signed_in.delete(CONNECTION).status_code == 404

    def test_reconnecting_after_disconnect_works(self, signed_in: TestClient, monkeypatch) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)
        signed_in.delete(CONNECTION)

        response = signed_in.post(CONNECTION, json=GOOD)

        assert response.json()["connected"] is True
        connection = stored()
        assert connection is not None
        assert connection.disconnected_at is None


class TestEnvironmentCredential:
    """Credentials read from .env — a development convenience, DB wins."""

    def test_the_env_store_shows_as_connected(self, env_shopify: TestClient) -> None:
        body = env_shopify.get(CONNECTION).json()

        assert body["connected"] is True
        assert body["source"] == "environment"
        assert body["connection"]["shop_domain"] == "envstore.myshopify.com"

    def test_the_env_store_has_no_row_id(self, env_shopify: TestClient) -> None:
        """Inventing one would imply the connection endpoints can act on it."""
        assert env_shopify.get(CONNECTION).json()["connection"]["id"] is None
        assert stored() is None

    def test_the_env_token_is_never_returned(self, env_shopify: TestClient) -> None:
        body = env_shopify.get(CONNECTION).text

        assert "shpat_from_the_environment_file" not in body
        assert "token" not in body.replace("token_scopes", "")

    def test_a_stored_connection_takes_precedence(
        self, env_shopify: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, happy)
        env_shopify.post(CONNECTION, json=GOOD)

        body = env_shopify.get(CONNECTION).json()

        assert body["source"] == "database"
        assert body["connection"]["shop_domain"] == "mystore.myshopify.com"

    def test_disconnecting_falls_back_to_the_env_store(
        self, env_shopify: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, happy)
        env_shopify.post(CONNECTION, json=GOOD)

        body = env_shopify.delete(CONNECTION).json()

        # The stored row is gone, but .env still names a store — showing the
        # empty state here would be a lie the next page load corrects.
        assert body["source"] == "environment"
        assert body["connection"]["shop_domain"] == "envstore.myshopify.com"

    def test_disconnecting_an_env_only_store_explains_itself(self, env_shopify: TestClient) -> None:
        response = env_shopify.delete(CONNECTION)

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "shopify_env_credential"
        assert ".env" in error["next"]

    def test_verify_tests_the_env_credential(self, env_shopify: TestClient, monkeypatch) -> None:
        seen = install(monkeypatch, happy)

        response = env_shopify.post(f"{CONNECTION}/verify")

        assert response.status_code == 200
        assert response.json()["source"] == "environment"
        assert any("envstore.myshopify.com" in url for url in seen)

    def test_a_broken_env_credential_reports_the_failure(
        self, env_shopify: TestClient, monkeypatch
    ) -> None:
        install(monkeypatch, lambda url, **_: FakeResponse(401))

        response = env_shopify.post(f"{CONNECTION}/verify")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "shopify_auth_failed"

    def test_an_incomplete_pair_is_not_a_connection(self, signed_in: TestClient) -> None:
        """A URL with no token cannot call anything, so it is not 'configured'."""
        signed_in.app.state.settings.shopify_store_url = "envstore.myshopify.com"
        signed_in.app.state.settings.shopify_admin_api_token = ""

        assert signed_in.get(CONNECTION).json()["connected"] is False

    def test_production_ignores_the_env_credential(self, env_shopify: TestClient) -> None:
        """A plaintext token on disk is a development convenience only."""
        env_shopify.app.state.settings.env = "production"

        body = env_shopify.get(CONNECTION).json()

        assert body["connected"] is False
        assert body["source"] == "none"


class TestSettingsNames:
    """The four Shopify settings use Shopify's own names, and only those."""

    def test_unprefixed_names_are_read(self, monkeypatch) -> None:
        monkeypatch.setenv("SHOPIFY_STORE_URL", "envstore.myshopify.com")
        monkeypatch.setenv("SHOPIFY_ADMIN_API_TOKEN", "shpat_unprefixed")
        monkeypatch.setenv("SHOPIFY_API_VERSION", "2024-10")
        monkeypatch.setenv("SHOPIFY_TIMEOUT_SECONDS", "30")

        settings = Settings(env="test")

        assert settings.shopify_store_url == "envstore.myshopify.com"
        assert settings.shopify_admin_api_token == "shpat_unprefixed"
        assert settings.shopify_api_version == "2024-10"
        assert settings.shopify_timeout_seconds == 30.0

    @pytest.mark.parametrize(
        ("variable", "attribute"),
        [
            ("STOCKSYNC_SHOPIFY_STORE_URL", "shopify_store_url"),
            ("STOCKSYNC_SHOPIFY_ADMIN_API_TOKEN", "shopify_admin_api_token"),
            ("STOCKSYNC_SHOPIFY_API_VERSION", "shopify_api_version"),
            ("STOCKSYNC_SHOPIFY_TIMEOUT_SECONDS", "shopify_timeout_seconds"),
        ],
    )
    def test_the_prefixed_spelling_is_not_read(
        self, monkeypatch, variable: str, attribute: str
    ) -> None:
        """One spelling per setting: a prefixed value is inert, not a fallback."""
        monkeypatch.setenv(variable, "2020-01" if "VERSION" in variable else "99")

        assert getattr(Settings(env="test"), attribute) == default_for(attribute)

    @pytest.mark.parametrize(
        ("variable", "attribute"),
        [
            ("SHOPIFY_API_VERSION", "shopify_api_version"),
            ("SHOPIFY_TIMEOUT_SECONDS", "shopify_timeout_seconds"),
        ],
    )
    def test_a_blank_value_falls_back_to_the_default(
        self, monkeypatch, variable: str, attribute: str
    ) -> None:
        """Clearing a line in .env must not stop the API booting."""
        monkeypatch.setenv(variable, "")

        assert getattr(Settings(env="test"), attribute) == default_for(attribute)

    def test_a_prefixed_credential_does_not_connect_a_store(self, monkeypatch) -> None:
        """The clearest failure mode of the old alias: a silently ignored pair."""
        monkeypatch.setenv("STOCKSYNC_SHOPIFY_STORE_URL", "ghost.myshopify.com")
        monkeypatch.setenv("STOCKSYNC_SHOPIFY_ADMIN_API_TOKEN", "shpat_ghost")

        assert Settings(env="test").has_env_shopify_credential is False

    def test_a_complete_pair_is_active_outside_production(self, monkeypatch) -> None:
        monkeypatch.setenv("SHOPIFY_STORE_URL", "s.myshopify.com")
        monkeypatch.setenv("SHOPIFY_ADMIN_API_TOKEN", "shpat_x")

        assert Settings(env="development").env_shopify_credential_active is True
        # A production Settings must satisfy the production invariants — secure
        # cookies among them — before any other property can be asked of it.
        production = Settings(
            env="production", jwt_secret="p" * 48, encryption_key="k", cookie_secure=True
        )
        assert production.env_shopify_credential_active is False
        # Still *present* in production — that is what the startup warning reads.
        assert production.has_env_shopify_credential is True


class TestCrypto:
    def test_ciphertext_round_trips(self) -> None:
        settings = Settings(env="test", encryption_key="")

        assert crypto.decrypt(settings, crypto.encrypt(settings, "secret")) == "secret"

    def test_ciphertext_differs_between_calls(self) -> None:
        """Fernet embeds a fresh IV, so identical plaintext is not identical output."""
        settings = Settings(env="test", encryption_key="")

        assert crypto.encrypt(settings, "same") != crypto.encrypt(settings, "same")

    def test_a_wrong_key_is_a_clean_error_not_a_crash(self) -> None:
        """A rotated STOCKSYNC_ENCRYPTION_KEY must say "reconnect", not 500."""
        ciphertext = crypto.encrypt(Settings(env="test", encryption_key=fernet_key()), "secret")
        other = Settings(env="test", encryption_key=fernet_key())

        with pytest.raises(crypto.CredentialUnreadableError):
            crypto.decrypt(other, ciphertext)

    def test_an_unset_key_is_stable_within_one_process(self) -> None:
        """Two Settings with no key share the per-process fallback, so dev works."""
        a = Settings(env="test", encryption_key="")
        b = Settings(env="test", encryption_key="")

        assert crypto.decrypt(b, crypto.encrypt(a, "secret")) == "secret"

    def test_a_malformed_key_names_the_variable(self) -> None:
        with pytest.raises(ValueError, match="STOCKSYNC_ENCRYPTION_KEY"):
            crypto.encrypt(Settings(env="test", encryption_key="not-a-fernet-key"), "x")


def orders_only(url: str, **_: Any) -> FakeResponse:
    """The real store's token: authenticates, and grants exactly read_orders.

    This is the *sufficient* case. It used to be the deficient one, because
    REQUIRED_SCOPES still listed read_products after the catalogue that needed it
    was removed — so the one token the product actually works with was rejected.
    """
    if "access_scopes" in url:
        return FakeResponse(200, {"access_scopes": [{"handle": "read_orders"}]})
    return FakeResponse(200, SHOP_BODY)


def no_order_scope(url: str, **_: Any) -> FakeResponse:
    """Authenticates, but cannot read orders — the only deficiency left."""
    if "access_scopes" in url:
        return FakeResponse(200, {"access_scopes": [{"handle": "read_products"}]})
    return FakeResponse(200, SHOP_BODY)


class TestEnvCredentialIsVerifiedBeforeItIsTrusted:
    """The regression that hid a missing scope for the life of the project.

    Adoption used to write ``status="connected"`` with ``last_verified_at``
    stamped without calling Shopify at all, so a token that could not read
    products looked exactly like a healthy one.
    """

    def test_adoption_actually_calls_shopify(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = install(monkeypatch, happy)

        env_shopify.post(SYNC)

        assert any("shop.json" in url for url in seen), "adoption never verified the credential"
        assert any("access_scopes" in url for url in seen), "adoption never read the scopes"

    def test_the_granted_scopes_are_recorded(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without these the Connection page cannot show what the token can do."""
        install(monkeypatch, happy)

        env_shopify.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.token_scopes is not None
        assert "read_products" in connection.token_scopes

    def test_the_store_profile_is_recorded(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, happy)

        env_shopify.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.store_name == "Deodap Retail"
        assert connection.currency == "INR"

    def test_a_scope_deficient_token_is_still_adopted(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Orders still sync with an orders-only token; refusing would lose that."""
        install(monkeypatch, orders_only)

        env_shopify.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.access_token_encrypted

    def test_a_read_orders_token_is_connected(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read_orders is the whole requirement, so this token is not deficient."""
        install(monkeypatch, orders_only)

        env_shopify.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.status == "connected"
        assert connection.store_name == "Deodap Retail"
        assert connection.plan_name == "Shopify Plus"
        assert connection.currency == "INR"

    def test_a_token_that_cannot_read_orders_is_labelled_honestly(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not "connected" — the page would be claiming a capability it lacks."""
        install(monkeypatch, no_order_scope)

        env_shopify.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.status == "missing_scopes"

    def test_the_scopes_it_does_have_are_still_recorded(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So the page can show what is granted, not just what is missing."""
        install(monkeypatch, no_order_scope)

        env_shopify.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.token_scopes == "read_products"

    def test_a_credential_that_cannot_authenticate_is_not_stored(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dead row would make the app carry a credential nothing can use."""

        def rejected(url: str, **_: Any) -> FakeResponse:
            return FakeResponse(401, {"errors": "Invalid API key or access token"})

        install(monkeypatch, rejected)

        env_shopify.post(SYNC)

        assert stored() is None


class TestScopeFailureIsNotCalledAnExpiredToken:
    """Regression: a missing scope was reported as ``token_expired``.

    That sends the user to regenerate a token that works perfectly, when the
    fix is granting a scope in the Shopify admin — a dead end costing a round
    trip for nothing.
    """

    def test_a_scope_failure_is_labelled_missing_scopes(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        def orders_denied(url: str, **_: Any) -> FakeResponse:
            # The order stage is the only stage now, so this is where a missing
            # scope surfaces during a sync.
            if "orders" in url:
                return FakeResponse(403, {"errors": "requires merchant approval"})
            if "access_scopes" in url:
                return FakeResponse(200, SCOPES_BODY)
            return FakeResponse(200, SHOP_BODY)

        install(monkeypatch, orders_denied)
        signed_in.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.status == "missing_scopes"
        assert connection.status != "token_expired"

    def test_a_genuine_auth_failure_is_still_token_expired(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The distinction only helps if the other side still works."""
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        def revoked(url: str, **_: Any) -> FakeResponse:
            if "shop.json" in url or "access_scopes" in url:
                return FakeResponse(200, SHOP_BODY)
            return FakeResponse(401, {"errors": "Invalid API key or access token"})

        install(monkeypatch, revoked)
        signed_in.post(SYNC)

        connection = stored()
        assert connection is not None
        assert connection.status == "token_expired"


class TestConnectionFieldsArePersisted:
    """Test Connection must *store* what it fetched, not just display it.

    A field that reaches the confirmation card but never the row leaves the
    Connection page blank on the next load, with nothing to show the user about
    a credential the app is relying on.
    """

    def test_saving_persists_every_profile_field(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, happy)

        signed_in.post(CONNECTION, json=GOOD)

        connection = stored()
        assert connection is not None
        assert connection.store_name == "Deodap Retail"
        assert connection.plan_name == "Shopify Plus"
        assert connection.currency == "INR"
        assert connection.token_scopes == "read_products,read_orders"
        assert connection.last_verified_at is not None

    def test_re_testing_an_existing_connection_refreshes_them(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The button on the Store card, after the store was renamed in Shopify."""
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        def renamed(url: str, **_: Any) -> FakeResponse:
            if "access_scopes" in url:
                return FakeResponse(
                    200,
                    {"access_scopes": [{"handle": h} for h in ("read_products", "read_orders")]},
                )
            return FakeResponse(
                200,
                {
                    "shop": {
                        "name": "DeoDap Wholesale",
                        "plan_display_name": "Shopify Advanced",
                        "currency": "USD",
                    }
                },
            )

        install(monkeypatch, renamed)
        response = signed_in.post(f"{CONNECTION}/verify")

        assert response.status_code == 200
        connection = stored()
        assert connection is not None
        assert connection.store_name == "DeoDap Wholesale"
        assert connection.plan_name == "Shopify Advanced"
        assert connection.currency == "USD"

    def test_last_verified_at_moves_forward_on_a_re_test(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)
        first = stored()
        assert first is not None
        before = first.last_verified_at

        signed_in.post(f"{CONNECTION}/verify")

        after = stored()
        assert after is not None
        assert after.last_verified_at is not None
        assert before is not None
        assert after.last_verified_at >= before

    def test_a_read_orders_token_is_accepted_by_the_form(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real store's token, and the whole point of the scope change.

        This is the regression that made the live store unconnectable: the form
        refused the only credential the product can actually use.
        """
        install(monkeypatch, orders_only)

        response = signed_in.post(CONNECTION, json=GOOD)

        assert response.status_code == 200, response.json()
        connection = stored()
        assert connection is not None
        assert connection.status == "connected"
        assert connection.token_scopes == "read_orders"

    def test_a_token_that_cannot_read_orders_is_refused_by_the_form(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The form must not store a credential that cannot do the job."""
        install(monkeypatch, no_order_scope)

        response = signed_in.post(CONNECTION, json=GOOD)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "shopify_missing_scopes"
        assert response.json()["error"]["detail"]["missing_scopes"] == ["read_orders"]
        assert stored() is None

    def test_the_scope_failure_names_only_read_orders(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Asking for read_products would send the user after a scope nothing calls."""
        install(monkeypatch, no_order_scope)

        error = signed_in.post(CONNECTION, json=GOOD).json()["error"]

        assert "read_products" not in error["next"]
        assert "read_orders" in error["next"]


class TestVerifyRecordsWhyItFailed:
    """Test Connection must distinguish the two credential failures.

    Both used to write ``token_expired``, which for a scope problem sends the
    user to regenerate a token that works — a dead end, since the fix is a scope
    grant in the Shopify admin.
    """

    def test_a_scope_failure_records_missing_scopes(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        install(monkeypatch, no_order_scope)
        response = signed_in.post(f"{CONNECTION}/verify")

        assert response.status_code == 400
        connection = stored()
        assert connection is not None
        assert connection.status == "missing_scopes"

    def test_it_records_the_scopes_that_are_granted(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So the Store card can show what is available, not just that it failed."""
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        install(monkeypatch, no_order_scope)
        signed_in.post(f"{CONNECTION}/verify")

        connection = stored()
        assert connection is not None
        assert connection.token_scopes == "read_products"

    def test_a_revoked_token_still_records_token_expired(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, happy)
        signed_in.post(CONNECTION, json=GOOD)

        def revoked(url: str, **_: Any) -> FakeResponse:
            return FakeResponse(401, {"errors": "Invalid API key or access token"})

        install(monkeypatch, revoked)
        signed_in.post(f"{CONNECTION}/verify")

        connection = stored()
        assert connection is not None
        assert connection.status == "token_expired"
