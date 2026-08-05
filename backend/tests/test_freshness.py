"""Staleness measured against the live store, not against our own rollup.

The old signal compared ``sku_daily_metrics.computed_at`` against
``orders.synced_at``: it says whether the derived layer has caught up with the
rows we hold, and nothing at all about whether those rows have caught up with
Shopify. On the real store it read "current" while the database was 34 hours and
10,731 orders behind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto
from app.db import session as session_module
from app.models import Order, ShopifyConnection, utcnow
from app.services import sync as sync_service
from app.services.sync import FRESHNESS_TOLERANCE, Freshness, freshness_from

FRESHNESS = "/api/shopify/freshness"
SHOP_BODY = {"shop": {"name": "Deodap", "plan_display_name": "Plus", "currency": "INR"}}
SCOPES_BODY = {"access_scopes": [{"handle": "read_orders"}]}


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._payload


def serve(
    monkeypatch: pytest.MonkeyPatch, newest: str | None, *, fail: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    """Answer Shopify's endpoints; `newest` is the store's newest order.

    Records (url, params). The client passes query parameters to httpx
    separately, so reading them off the URL string would find nothing.
    """
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(url: str, **kwargs: Any) -> FakeResponse:
        seen.append((url, kwargs.get("params") or {}))
        if fail:
            raise httpx.ConnectError("down")
        if "access_scopes" in url:
            return FakeResponse(200, SCOPES_BODY)
        if "shop.json" in url:
            return FakeResponse(200, SHOP_BODY)
        if "orders.json" in url:
            orders = [{"processed_at": newest}] if newest else []
            return FakeResponse(200, {"orders": orders})
        return FakeResponse(200, {})

    monkeypatch.setattr(httpx, "get", handler)
    return seen


def connect(client: TestClient) -> ShopifyConnection:
    """A stored connection, without going through the network."""
    with session_module.get_session_factory()() as db:
        connection = ShopifyConnection(
            workspace_id=1,
            shop_domain="s.myshopify.com",
            access_token_encrypted=crypto.encrypt(client.app.state.settings, "shpat_test"),
            status="connected",
            connected_at=utcnow(),
        )
        db.add(connection)
        db.commit()
        db.refresh(connection)
        return connection


def add_order(client: TestClient, connection_id: int, processed: datetime) -> None:
    with session_module.get_session_factory()() as db:
        seed = db.scalar(select(Order).order_by(Order.id.desc()))
        db.add(
            Order(
                workspace_id=1,
                connection_id=connection_id,
                shopify_order_id=(seed.shopify_order_id + 1) if seed else 90_001,
                processed_at=processed,
                created_at_shopify=processed,
                financial_status="paid",
                synced_at=utcnow(),
            )
        )
        db.commit()


def stored() -> ShopifyConnection | None:
    with session_module.get_session_factory()() as db:
        return db.scalars(select(ShopifyConnection)).first()


class TestTheArithmetic:
    """`freshness_from` is pure, so the comparison is tested without a network."""

    def now(self) -> datetime:
        return datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    def make(self, store_gap: timedelta | None) -> ShopifyConnection:
        connection = ShopifyConnection(
            workspace_id=1, shop_domain="s.myshopify.com", access_token_encrypted=""
        )
        connection.store_latest_order_at = self.now() + store_gap if store_gap is not None else None
        return connection

    def test_behind_when_the_store_is_ahead(self) -> None:
        result = freshness_from(self.make(timedelta(hours=34)), self.now())

        assert result.behind is True
        assert result.behind_seconds == 34 * 3600
        assert result.behind_hours == 34.0

    def test_not_behind_inside_the_tolerance(self) -> None:
        """A sync takes minutes and orders keep arriving; small gaps are normal."""
        result = freshness_from(self.make(FRESHNESS_TOLERANCE - timedelta(minutes=1)), self.now())

        assert result.behind is False

    def test_behind_just_outside_the_tolerance(self) -> None:
        result = freshness_from(self.make(FRESHNESS_TOLERANCE + timedelta(minutes=1)), self.now())

        assert result.behind is True

    def test_never_behind_when_we_are_ahead(self) -> None:
        """Clock skew must not report a negative gap as a problem."""
        result = freshness_from(self.make(timedelta(hours=-2)), self.now())

        assert result.behind is False
        assert result.behind_seconds == 0

    def test_unknown_when_the_store_was_never_read(self) -> None:
        """None is not False: "we do not know" must not read as "we are current"."""
        result = freshness_from(self.make(None), self.now())

        assert result.behind is None
        assert result.behind_hours is None

    def test_behind_when_nothing_has_ever_synced(self) -> None:
        result = freshness_from(self.make(timedelta(0)), None)

        assert result.behind is True

    def test_no_connection_is_unknown(self) -> None:
        assert freshness_from(None, self.now()).behind is None

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        """SQLite hands back naive datetimes; comparing them raw would raise."""
        connection = self.make(timedelta(hours=5))
        connection.store_latest_order_at = datetime(2026, 7, 30, 17, 0)  # naive

        result = freshness_from(connection, datetime(2026, 7, 30, 12, 0))

        assert result.behind is True
        assert result.behind_seconds == 5 * 3600


class TestTheEndpoint:
    def test_it_requires_a_session(self, api: TestClient) -> None:
        assert api.get(FRESHNESS).status_code == 401

    def test_it_reports_the_gap_and_records_it(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = connect(signed_in)
        ours = datetime.now(UTC) - timedelta(days=2)
        add_order(signed_in, connection.id, ours)
        serve(monkeypatch, datetime.now(UTC).isoformat())

        body = signed_in.get(FRESHNESS).json()

        assert body["behind"] is True
        assert body["behind_hours"] >= 47
        assert body["store_latest_order_at"] is not None
        assert body["synced_through"] is not None
        # Recorded, so other screens can report it without paying for the call.
        row = stored()
        assert row is not None and row.store_latest_order_at is not None
        assert row.freshness_checked_at is not None

    def test_it_reports_current_when_it_is(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = connect(signed_in)
        now = datetime.now(UTC)
        add_order(signed_in, connection.id, now)
        serve(monkeypatch, now.isoformat())

        body = signed_in.get(FRESHNESS).json()

        assert body["behind"] is False
        assert body["behind_seconds"] == 0

    def test_one_shopify_request_not_a_page_of_them(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It runs on a page load, so it must not walk the order pages."""
        connection = connect(signed_in)
        add_order(signed_in, connection.id, datetime.now(UTC))
        seen = serve(monkeypatch, datetime.now(UTC).isoformat())

        signed_in.get(FRESHNESS)

        assert len(seen) == 1
        url, params = seen[0]
        assert "orders.json" in url
        # One order, newest first — not a page walk.
        assert params["limit"] == 1
        assert params["order"] == "processed_at desc"
        assert params["status"] == "any"

    def test_an_unreachable_store_is_unknown_not_current(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection = connect(signed_in)
        add_order(signed_in, connection.id, datetime.now(UTC) - timedelta(days=3))
        serve(monkeypatch, None, fail=True)

        body = signed_in.get(FRESHNESS).json()

        assert body["behind"] is None
        assert body["synced_through"] is not None

    def test_a_store_with_no_orders_is_unknown(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connect(signed_in)
        serve(monkeypatch, None)

        assert signed_in.get(FRESHNESS).json()["behind"] is None

    def test_the_stored_value_is_left_alone_when_shopify_fails(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed check must not erase the last good answer."""
        connection = connect(signed_in)
        add_order(signed_in, connection.id, datetime.now(UTC) - timedelta(days=1))
        serve(monkeypatch, datetime.now(UTC).isoformat())
        signed_in.get(FRESHNESS)
        first = stored()
        assert first is not None
        recorded = first.store_latest_order_at

        serve(monkeypatch, None, fail=True)
        signed_in.get(FRESHNESS)

        again = stored()
        assert again is not None
        assert again.store_latest_order_at == recorded

    def test_with_no_credential_at_all_it_still_answers(self, signed_in: TestClient) -> None:
        body = signed_in.get(FRESHNESS).json()

        assert body["behind"] is None
        assert body["store_latest_order_at"] is None

    def test_an_env_only_store_reports_the_gap_without_a_row_to_store_it_on(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve(monkeypatch, datetime.now(UTC).isoformat())

        body = env_shopify.get(FRESHNESS).json()

        # Nothing has synced, so the store being ahead is the whole finding.
        assert body["behind"] is True
        assert body["store_latest_order_at"] is not None


class TestItRunsAfterASync:
    def test_a_sync_records_where_the_store_had_got_to(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The client is already authenticated there, so it is one extra call."""
        newest = datetime.now(UTC).replace(microsecond=0)

        def handler(url: str, **kwargs: Any) -> FakeResponse:
            params = kwargs.get("params") or {}
            if "access_scopes" in url:
                return FakeResponse(200, SCOPES_BODY)
            if "shop.json" in url:
                return FakeResponse(200, SHOP_BODY)
            if "count.json" in url:
                return FakeResponse(200, {"count": 1})
            if "orders.json" in url and params.get("limit") == 1:
                return FakeResponse(200, {"orders": [{"processed_at": newest.isoformat()}]})
            if "orders.json" in url:
                return FakeResponse(
                    200,
                    {
                        "orders": [
                            {
                                "id": 5001,
                                "processed_at": newest.isoformat(),
                                "created_at": newest.isoformat(),
                                "financial_status": "paid",
                                "line_items": [{"id": 9001, "sku": "DD-1", "quantity": 2}],
                            }
                        ]
                    },
                )
            return FakeResponse(200, {})

        monkeypatch.setattr(httpx, "get", handler)

        env_shopify.post("/api/shopify/sync")

        row = stored()
        assert row is not None
        assert row.store_latest_order_at is not None
        assert row.freshness_checked_at is not None

    def test_a_freshness_failure_does_not_fail_the_sync(
        self, env_shopify: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not knowing how fresh we are is not a reason to discard synced orders."""
        calls = {"n": 0}

        def handler(url: str, **kwargs: Any) -> FakeResponse:
            params = kwargs.get("params") or {}
            if "access_scopes" in url:
                return FakeResponse(200, SCOPES_BODY)
            if "shop.json" in url:
                return FakeResponse(200, SHOP_BODY)
            if "count.json" in url:
                return FakeResponse(200, {"count": 1})
            if "orders.json" in url and params.get("limit") == 1:
                calls["n"] += 1
                raise httpx.ConnectError("down")
            if "orders.json" in url:
                return FakeResponse(
                    200,
                    {
                        "orders": [
                            {
                                "id": 5002,
                                "processed_at": datetime.now(UTC).isoformat(),
                                "created_at": datetime.now(UTC).isoformat(),
                                "financial_status": "paid",
                                "line_items": [{"id": 9002, "sku": "DD-1", "quantity": 1}],
                            }
                        ]
                    },
                )
            return FakeResponse(200, {})

        monkeypatch.setattr(httpx, "get", handler)

        env_shopify.post("/api/shopify/sync")

        run = env_shopify.get("/api/shopify/syncs").json()["items"][0]
        assert run["result"] == "success"
        assert run["orders_synced"] == 1
        assert calls["n"] >= 1  # it was attempted


def test_the_service_returns_the_dataclass(
    signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route is a thin shell; the shape belongs to the service."""
    connect(signed_in)
    serve(monkeypatch, datetime.now(UTC).isoformat())

    with session_module.get_session_factory()() as db:
        result = sync_service.check_freshness(db, signed_in.app.state.settings, workspace_id=1)

    assert isinstance(result, Freshness)


def test_a_locked_database_still_reports_the_gap(
    signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync holds SQLite's write lock in bursts, so this small write can lose.

    Failing to *record* the answer is not a reason to withhold it — the value was
    read from Shopify a moment ago and is exactly what was asked for.
    """
    from sqlalchemy.exc import OperationalError

    connection = connect(signed_in)
    add_order(signed_in, connection.id, datetime.now(UTC) - timedelta(days=2))
    serve(monkeypatch, datetime.now(UTC).isoformat())

    real_commit = Session.commit
    calls = {"n": 0}

    def flaky(self: Session) -> None:
        calls["n"] += 1
        # The first commit is the freshness write; let everything else through.
        if calls["n"] == 1:
            raise OperationalError("UPDATE shopify_connections", {}, Exception("locked"))
        real_commit(self)

    monkeypatch.setattr(Session, "commit", flaky)

    body = signed_in.get(FRESHNESS).json()

    assert body["behind"] is True
    assert body["store_latest_order_at"] is not None
    assert body["behind_hours"] >= 47
