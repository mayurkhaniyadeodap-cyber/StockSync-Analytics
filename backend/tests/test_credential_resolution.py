"""Which Shopify credential a workspace uses, and who agrees about it.

A stored row is authoritative the moment it exists. `.env` is a bootstrap for a
workspace that has never connected a store, not a standby for one whose
connection has gone wrong.

The rule used to be "a row that is *connected* wins", and the consequence was
specific: a row that had expired or been disconnected fell through to `.env`,
which names an arbitrary store compared against nothing. The Connection page
reported the `.env` store as healthy, the freshness check measured the `.env`
store, and the sync went on using the saved store's own expired token and
failed. Three components, two different stores, and the one being reported was
not the one being synced.

So the assertions here are mostly about *agreement*: the page, the resolver and
the sync must name one store, in every state a connection can be in.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import session as session_module
from app.models import ShopifyConnection
from app.services import shopify as shopify_service

CONNECTION = "/api/shopify/connection"

#: The store the `.env` pair names, which is deliberately *not* the stored one.
ENV_DOMAIN = "envstore.myshopify.com"
STORED_DOMAIN = "mystore.myshopify.com"


def stored_row() -> ShopifyConnection | None:
    with session_module.get_session_factory()() as db:
        return db.scalar(select(ShopifyConnection))


def set_status(status: str, *, blank_token: bool = False) -> None:
    """Put the stored row into a state, the way Shopify would have."""
    with session_module.get_session_factory()() as db:
        connection = db.scalar(select(ShopifyConnection))
        assert connection is not None
        connection.status = status
        if blank_token:
            # What `disconnect()` does: the ciphertext is overwritten, so there
            # is no token left to send.
            connection.access_token_encrypted = ""
        db.commit()


def resolve(settings: Settings) -> shopify_service.ResolvedCredential | None:
    with session_module.get_session_factory()() as db:
        return shopify_service.resolve_credential(db, settings, workspace_id=1)


@pytest.fixture
def connected(env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A stored connection **and** an active `.env` pair naming another store.

    Both present at once is the only configuration in which the old fallback
    could do damage, and the only one in which the new rule is worth asserting.
    """
    from tests.test_shopify_api import GOOD, happy, install

    install(monkeypatch, happy)
    env_shopify.post(CONNECTION, json=GOOD)
    assert stored_row() is not None
    yield env_shopify


class TestAStoredRowIsAuthoritative:
    @pytest.mark.parametrize("status", ["token_expired", "missing_scopes"])
    def test_a_broken_row_does_not_fall_back_to_env(
        self, connected: TestClient, status: str
    ) -> None:
        """The saved store, with its real status — never the `.env` one."""
        set_status(status)

        credential = resolve(connected.app.state.settings)

        assert credential is not None
        assert credential.source == "database"
        assert credential.shop_domain == STORED_DOMAIN
        assert credential.status == status

    @pytest.mark.parametrize("status", ["token_expired", "missing_scopes"])
    def test_a_broken_row_is_still_usable(self, connected: TestClient, status: str) -> None:
        """Deliberately. An expired token is how a credential that has started
        working again heals — the next call succeeds and the status is
        rewritten — and a missing scope is a partial capability the sync is
        designed to exploit rather than refuse."""
        set_status(status)

        credential = resolve(connected.app.state.settings)

        assert credential is not None
        assert credential.usable is True

    def test_a_disconnected_row_does_not_fall_back_to_env(self, connected: TestClient) -> None:
        set_status("disconnected", blank_token=True)

        credential = resolve(connected.app.state.settings)

        assert credential is not None
        assert credential.source == "database"
        assert credential.shop_domain == STORED_DOMAIN
        assert credential.usable is False

    def test_env_is_used_when_there_is_no_row(self, env_shopify: TestClient) -> None:
        """The bootstrap case, which is the whole reason `.env` exists."""
        assert stored_row() is None

        credential = resolve(env_shopify.app.state.settings)

        assert credential is not None
        assert credential.source == "environment"
        assert credential.shop_domain == ENV_DOMAIN

    def test_no_row_and_no_env_is_no_credential(self, signed_in: TestClient) -> None:
        assert resolve(signed_in.app.state.settings) is None


class TestEveryPathNamesOneStore:
    """The page, the resolver and the sync, in each state a row can reach."""

    @pytest.mark.parametrize(
        ("status", "blank_token"),
        [("connected", False), ("token_expired", False), ("missing_scopes", False)],
    )
    def test_the_page_reports_the_stored_store(
        self, connected: TestClient, status: str, blank_token: bool
    ) -> None:
        set_status(status, blank_token=blank_token)

        body = connected.get(CONNECTION).json()

        assert body["source"] == "database"
        assert body["connection"]["shop_domain"] == STORED_DOMAIN
        assert body["connection"]["status"] == status

    def test_the_page_reports_a_disconnected_row_as_disconnected(
        self, connected: TestClient
    ) -> None:
        set_status("disconnected", blank_token=True)

        body = connected.get(CONNECTION).json()

        assert body["source"] == "database"
        assert body["connected"] is False
        assert body["connection"]["status"] == "disconnected"

    @pytest.mark.parametrize("status", ["connected", "token_expired", "missing_scopes"])
    def test_the_page_and_the_resolver_agree(self, connected: TestClient, status: str) -> None:
        """The assertion the old behaviour could not pass: what the page shows
        is what the sync would use."""
        set_status(status)

        body = connected.get(CONNECTION).json()
        credential = resolve(connected.app.state.settings)

        assert credential is not None
        assert body["connection"]["shop_domain"] == credential.shop_domain
        assert body["source"] == credential.source

    def test_a_sync_refuses_a_disconnected_row_rather_than_using_env(
        self, connected: TestClient
    ) -> None:
        """`start_sync` has always refused here. What has changed is that the
        page now says the same thing."""
        set_status("disconnected", blank_token=True)

        response = connected.post("/api/shopify/sync")

        assert response.status_code in (404, 409)
        assert response.json()["error"]["code"] in (
            "shopify_not_connected",
            "shopify_env_credential",
        )
        assert connected.get(CONNECTION).json()["connection"]["shop_domain"] == STORED_DOMAIN


class TestTheStartupWarning:
    def test_a_mismatch_is_reported(
        self, connected: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Harmless now — the stored row wins — but silence here is what made
        the old fallback so hard to see."""
        from app.main import _warn_on_shop_domain_mismatch

        with caplog.at_level("WARNING"):
            _warn_on_shop_domain_mismatch(connected.app.state.settings)

        # `getMessage()` rather than `message % args`: other records in the
        # capture carry no args, and `%` on those raises.
        messages = [record.getMessage() for record in caplog.records]
        assert any(ENV_DOMAIN in message for message in messages)
        assert any(STORED_DOMAIN in message for message in messages)

    def test_matching_domains_say_nothing(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        from tests.test_shopify_api import GOOD, happy, install

        install(monkeypatch, happy)
        env_shopify.post(CONNECTION, json=GOOD)
        settings = env_shopify.app.state.settings
        settings.shopify_store_url = STORED_DOMAIN

        from app.main import _warn_on_shop_domain_mismatch

        with caplog.at_level("WARNING"):
            _warn_on_shop_domain_mismatch(settings)

        assert not [r for r in caplog.records if "shopify: .env names" in r.message]

    def test_no_stored_row_says_nothing(
        self, env_shopify: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """There is nothing to disagree with yet."""
        from app.main import _warn_on_shop_domain_mismatch

        with caplog.at_level("WARNING"):
            _warn_on_shop_domain_mismatch(env_shopify.app.state.settings)

        assert not [r for r in caplog.records if "shopify: .env names" in r.message]
