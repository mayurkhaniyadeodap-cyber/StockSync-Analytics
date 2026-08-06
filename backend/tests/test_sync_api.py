"""Shopify sync: pagination, batching, partial outcomes and history.

Shopify is stubbed at ``httpx.get``. Jobs run inline under STOCKSYNC_ENV=test
(see app/main.py), so a sync has finished by the time the POST returns and no
test races a worker thread.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core import crypto
from app.db import session as session_module
from app.models import (
    Order,
    OrderLineItem,
    ShopifyConnection,
    SyncRun,
    normalize_sku,
    utcnow,
)

SYNC = "/api/shopify/sync"
SYNCS = "/api/shopify/syncs"


# ---------------------------------------------------------------------------
# stub Shopify
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.url = "https://stub/"

    def json(self) -> Any:
        return self._payload


def link_header(cursor: str) -> dict[str, str]:
    """The Link header Shopify sends for a next page."""
    return {
        "Link": f"<https://s.myshopify.com/admin/api/2026-07/products.json"
        f'?limit=250&page_info={cursor}>; rel="next"'
    }


def order(oid: int, *, sku: str, quantity: int = 2, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": oid,
        "name": f"#{oid}",
        "created_at": "2026-07-01T10:00:00+05:30",
        "processed_at": "2026-07-01T10:00:00+05:30",
        "financial_status": "paid",
        "currency": "INR",
        "total_price": "399.00",
        "line_items": [
            {
                "id": oid * 100,
                "variant_id": 1010,
                "sku": sku,
                "title": "Thing",
                "quantity": quantity,
                "price": "199.50",
                "total_discount": "0.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


class Shopify:
    """A scripted store. Routes by path, records what was asked for."""

    def __init__(
        self,
        *,
        orders: list[list[dict[str, Any]]] | None = None,
        order_status: int | None = None,
    ) -> None:
        self.order_pages = orders if orders is not None else [[]]
        self.order_status = order_status
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        params = kwargs.get("params") or {}
        self.requests.append((url, params))

        if "count.json" in url:
            return FakeResponse(200, {"count": sum(len(p) for p in self.order_pages)})

        if "orders.json" in url:
            if self.order_status:
                return FakeResponse(self.order_status)
            return self._page(self.order_pages, "orders", params)

        return FakeResponse(200, {})

    def _page(
        self, pages: list[list[dict[str, Any]]], key: str, params: dict[str, Any]
    ) -> FakeResponse:
        cursor = params.get("page_info")
        index = int(cursor.split("-")[-1]) if cursor else 0
        items = pages[index] if index < len(pages) else []
        headers = link_header(f"cursor-{index + 1}") if index + 1 < len(pages) else {}
        return FakeResponse(200, {key: items}, headers)


@pytest.fixture
def shopify(monkeypatch: pytest.MonkeyPatch):
    """Install a scripted store; returns a setter so a test can script it."""

    holder: dict[str, Shopify] = {}

    def install(store: Shopify) -> Shopify:
        holder["store"] = store
        monkeypatch.setattr(httpx, "get", store)
        return store

    install(Shopify())
    return install


@pytest.fixture
def connected(signed_in: TestClient) -> TestClient:
    """A workspace with a stored, encrypted Shopify credential."""
    settings = signed_in.app.state.settings
    with session_module.get_session_factory()() as db:
        db.add(
            ShopifyConnection(
                workspace_id=1,
                shop_domain="s.myshopify.com",
                access_token_encrypted=crypto.encrypt(settings, "example-token"),
                status="connected",
                connected_at=utcnow(),
                order_lookback_days=90,
            )
        )
        db.commit()
    return signed_in


def rows(model: Any) -> list[Any]:
    with session_module.get_session_factory()() as db:
        return list(db.scalars(select(model).order_by(model.id)))


# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_endpoints_require_a_session(self, api: TestClient) -> None:
        assert api.post(SYNC).status_code == 401
        assert api.get(SYNC).status_code == 401
        assert api.get(SYNCS).status_code == 401


class TestStartingASync:
    def test_syncing_without_a_connection_is_404(self, signed_in: TestClient) -> None:
        response = signed_in.post(SYNC)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "shopify_not_connected"

    def test_a_sync_pulls_orders_and_line_items(self, connected: TestClient, shopify) -> None:
        """The whole sync now. Products are not fetched at all — order line
        items carry ``sku_at_sale``, which is what a sheet SKU matches on."""
        shopify(Shopify(orders=[[order(1, sku="DD-1"), order(2, sku="DD-2")]]))

        body = connected.post(SYNC).json()

        assert body["run"]["result"] == "success"
        assert body["run"]["orders_synced"] == 2
        assert body["run"]["line_items_synced"] == 2
        assert len(rows(Order)) == 2
        assert len(rows(OrderLineItem)) == 2

    def test_the_product_endpoint_is_never_called(self, connected: TestClient, shopify) -> None:
        """The point of the change: with no products.json request, a token that
        lacks read_products syncs sales perfectly."""
        store = shopify(Shopify(orders=[[order(1, sku="DD-1")]]))

        connected.post(SYNC)

        assert not any("products" in url for url, _ in store.requests)

    def test_line_items_carry_the_sku_normalised_the_way_imports_are(
        self, connected: TestClient, shopify
    ) -> None:
        """Both sides normalise identically or the SKU join silently misses."""
        shopify(Shopify(orders=[[order(1, sku="dd 1001")]]))

        connected.post(SYNC)

        line = rows(OrderLineItem)[0]
        assert line.sku_at_sale == "dd 1001"
        assert line.sku_normalized == normalize_sku("dd 1001")

    def test_only_one_sync_runs_at_a_time(self, connected: TestClient, shopify) -> None:
        """A queued row that never finished must block a second start."""
        with session_module.get_session_factory()() as db:
            db.add(
                SyncRun(
                    workspace_id=1,
                    connection_id=1,
                    trigger="manual",
                    status="running",
                    stage="products",
                    started_at=utcnow(),
                )
            )
            db.commit()

        response = connected.post(SYNC)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "sync_already_running"


class TestPagination:
    def test_every_page_is_followed(self, connected: TestClient, shopify) -> None:
        store = shopify(
            Shopify(
                orders=[
                    [order(1, sku="A")],
                    [order(2, sku="B")],
                    [order(3, sku="C")],
                ]
            )
        )

        connected.post(SYNC)

        assert len(rows(Order)) == 3
        # Page two and three must have been requested by cursor, not by filter.
        cursors = [p.get("page_info") for _, p in store.requests if "page_info" in p]
        assert cursors == ["cursor-1", "cursor-2"]

    def test_the_first_order_request_carries_the_filters(
        self, connected: TestClient, shopify
    ) -> None:
        """status=any or Shopify silently omits every completed sale."""
        store = shopify(Shopify(orders=[[order(1, sku="A")]]))

        connected.post(SYNC)

        first = next(p for url, p in store.requests if "orders.json" in url)
        assert first["status"] == "any"
        assert "created_at_min" in first

    def test_a_cursor_request_does_not_repeat_the_filters(
        self, connected: TestClient, shopify
    ) -> None:
        """Shopify rejects page_info sent alongside the filters that made it."""
        store = shopify(Shopify(orders=[[order(1, sku="A")], [order(2, sku="B")]]))

        connected.post(SYNC)

        paged = [p for url, p in store.requests if "orders.json" in url and "page_info" in p]
        assert paged and all("status" not in p for p in paged)


class TestPartialAndFailure:
    def test_a_revoked_order_scope_is_a_failure_not_a_partial(
        self, connected: TestClient, shopify
    ) -> None:
        """With one stage there is nothing left to be partial about."""
        shopify(Shopify(order_status=403))

        body = connected.post(SYNC).json()

        assert body["run"]["result"] == "failed"
        assert body["run"]["error_code"] == "shopify_missing_scopes"

    def test_nothing_landing_is_failed_not_partial(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(order_status=403))

        body = connected.post(SYNC).json()

        assert body["run"]["result"] == "failed"
        assert body["run"]["orders_synced"] == 0

    def test_a_rejected_token_marks_the_connection_expired(
        self, connected: TestClient, shopify
    ) -> None:
        """The stored status is what puts the red dot in the sidebar (§4)."""
        shopify(Shopify(order_status=401))

        connected.post(SYNC)

        with session_module.get_session_factory()() as db:
            connection = db.scalars(select(ShopifyConnection)).one()
            assert connection.status == "token_expired"

    def test_a_failed_stage_records_a_resume_cursor(self, connected: TestClient, shopify) -> None:
        """Page one lands, page two 403s — the next run must not redo page one."""

        class Flaky(Shopify):
            def __init__(self) -> None:
                super().__init__(orders=[[order(1, sku="A")], [order(2, sku="B")]])
                self.calls = 0

            def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
                if "orders.json" in url and (kwargs.get("params") or {}).get("page_info"):
                    return FakeResponse(403)
                return super().__call__(url, **kwargs)

        shopify(Flaky())

        body = connected.post(SYNC).json()

        assert body["run"]["result"] == "partial"
        assert len(rows(Order)) == 1
        run = rows(SyncRun)[-1]
        assert run.cursor_orders == "cursor-1"

    def test_the_next_run_resumes_from_that_cursor(self, connected: TestClient, shopify) -> None:
        with session_module.get_session_factory()() as db:
            db.add(
                SyncRun(
                    workspace_id=1,
                    connection_id=1,
                    trigger="manual",
                    status="finished",
                    stage="done",
                    result="partial",
                    started_at=utcnow(),
                    finished_at=utcnow(),
                    cursor_orders="cursor-1",
                )
            )
            db.commit()

        store = shopify(Shopify(orders=[[order(1, sku="A")], [order(2, sku="B")]]))
        connected.post(SYNC)

        first_order_call = next(p for url, p in store.requests if "orders.json" in url)
        assert first_order_call.get("page_info") == "cursor-1"


class TestRateLimiting:
    def test_a_429_is_retried_rather_than_failing_the_sync(
        self, connected: TestClient, monkeypatch
    ) -> None:
        calls = {"n": 0}

        def flaky(url: str, **kwargs: Any) -> FakeResponse:
            if "orders.json" in url:
                calls["n"] += 1
                if calls["n"] == 1:
                    return FakeResponse(429, headers={"Retry-After": "0.01"})
                return FakeResponse(200, {"orders": [order(1, sku="A")]})
            if "count.json" in url:
                return FakeResponse(200, {"count": 1})
            return FakeResponse(200, {})

        monkeypatch.setattr(httpx, "get", flaky)

        body = connected.post(SYNC).json()

        assert body["run"]["result"] == "success"
        assert len(rows(Order)) == 1


class TestSyncState:
    def test_state_is_empty_before_any_sync(self, connected: TestClient) -> None:
        body = connected.get(SYNC).json()

        assert body == {"running": False, "run": None, "last_synced_at": None}

    def test_state_reports_the_last_run(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(orders=[[order(1, sku="A")]]))
        connected.post(SYNC)

        body = connected.get(SYNC).json()

        assert body["running"] is False
        assert body["run"]["result"] == "success"
        assert body["last_synced_at"] is not None


class TestSyncHistory:
    def test_empty_history(self, connected: TestClient) -> None:
        body = connected.get(SYNCS).json()

        assert body["items"] == []
        assert body["total"] == 0

    def test_history_is_newest_first(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(orders=[[order(1, sku="A")]]))
        connected.post(SYNC)
        shopify(Shopify(orders=[[order(2, sku="B")]]))
        connected.post(SYNC)

        body = connected.get(SYNCS).json()

        assert body["total"] == 2
        assert body["items"][0]["id"] > body["items"][1]["id"]

    def test_result_filter(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(orders=[[order(1, sku="A")]]))
        connected.post(SYNC)
        shopify(Shopify(order_status=403))
        connected.post(SYNC)

        assert connected.get(SYNCS, params={"result": "success"}).json()["total"] == 1
        assert connected.get(SYNCS, params={"result": "failed"}).json()["total"] == 1

    def test_one_run_by_id(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(orders=[[order(1, sku="A")]]))
        run_id = connected.post(SYNC).json()["run"]["id"]

        response = connected.get(f"{SYNCS}/{run_id}")

        assert response.status_code == 200
        assert response.json()["id"] == run_id

    def test_unknown_id_is_404(self, connected: TestClient) -> None:
        response = connected.get(f"{SYNCS}/9999")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "sync_not_found"


class TestInterruptedRuns:
    """Only *abandoned* runs are closed out — a live one belongs to a worker.

    Reclaim used to close every queued or running row on the assumption that this
    process is the only one. Under `uvicorn --reload` that is never true, and a
    sync running in another process was being stamped `sync_interrupted` while it
    carried on working.
    """

    def add_stuck_run(self, *, age: timedelta, **fields: object) -> None:
        """A running row whose last write was `age` ago."""
        with session_module.get_session_factory()() as db:
            run = SyncRun(
                workspace_id=1,
                connection_id=1,
                trigger="manual",
                status="running",
                stage="orders",
                started_at=utcnow() - age,
                **fields,  # type: ignore[arg-type]
            )
            db.add(run)
            db.commit()
            # Written directly: `updated_at` is maintained by the ORM, so it
            # cannot be back-dated through the object.
            db.execute(
                text("UPDATE sync_runs SET updated_at = :when WHERE id = :id"),
                {"when": utcnow() - age, "id": run.id},
            )
            db.commit()

    def test_an_abandoned_run_is_reclaimed(self, connected: TestClient) -> None:
        from app.services.sync import reclaim_interrupted_runs

        self.add_stuck_run(age=timedelta(hours=1), orders_synced=6500, cursor_orders="cursor-27")

        with session_module.get_session_factory()() as db:
            assert reclaim_interrupted_runs(db) == 1

        run = rows(SyncRun)[-1]
        assert run.status == "finished"
        # Rows landed, so it is partial rather than failed.
        assert run.result == "partial"
        assert run.error_code == "sync_interrupted"
        # The cursor survives, so the next run resumes instead of restarting.
        assert run.cursor_orders == "cursor-27"

    def test_a_live_run_is_left_alone(self, connected: TestClient) -> None:
        """The regression: a sync in another process must not be stamped dead.

        It commits after every page, so a row written seconds ago belongs to a
        worker that is still going.
        """
        from app.services.sync import reclaim_interrupted_runs

        self.add_stuck_run(age=timedelta(seconds=5), orders_synced=250)

        with session_module.get_session_factory()() as db:
            assert reclaim_interrupted_runs(db) == 0

        run = rows(SyncRun)[-1]
        assert run.status == "running"
        assert run.error_code is None

    def test_a_run_just_inside_the_window_is_left_alone(self, connected: TestClient) -> None:
        from app.services.sync import STALE_RUN_AFTER, reclaim_interrupted_runs

        self.add_stuck_run(age=STALE_RUN_AFTER - timedelta(seconds=30))
        with session_module.get_session_factory()() as db:
            assert reclaim_interrupted_runs(db) == 0

    def test_a_run_just_outside_the_window_is_reclaimed(self, connected: TestClient) -> None:
        """The other side of the same boundary.

        Two tests rather than one because a workspace may now hold only one live
        run — `uq_sync_runs_one_live_per_workspace` — so seeding a second
        alongside the first is a state the database refuses, which is the point
        of the index.
        """
        from app.services.sync import STALE_RUN_AFTER, reclaim_interrupted_runs

        self.add_stuck_run(age=STALE_RUN_AFTER + timedelta(seconds=30))
        with session_module.get_session_factory()() as db:
            assert reclaim_interrupted_runs(db) == 1

    def test_starting_a_sync_clears_an_abandoned_run_without_a_restart(
        self, connected: TestClient, shopify
    ) -> None:
        """Otherwise a killed worker blocks Sync now until the server restarts."""
        self.add_stuck_run(age=timedelta(hours=2), orders_synced=100)

        assert connected.post(SYNC).status_code == 202

    def test_starting_a_sync_still_refuses_while_one_is_live(
        self, connected: TestClient, shopify
    ) -> None:
        self.add_stuck_run(age=timedelta(seconds=5), orders_synced=100)

        assert connected.post(SYNC).status_code == 409

    def test_a_reclaimed_run_no_longer_blocks_a_new_sync(
        self, connected: TestClient, shopify
    ) -> None:
        """The whole sequence: live blocks, abandoned does not.

        The row is aged rather than created fresh, because "abandoned" is now a
        property of when it last wrote — not merely of the process having changed.
        """
        self.add_stuck_run(age=timedelta(seconds=5))
        assert connected.post(SYNC).status_code == 409

        with session_module.get_session_factory()() as db:
            db.execute(
                text("UPDATE sync_runs SET updated_at = :when, started_at = :when"),
                {"when": utcnow() - timedelta(hours=1)},
            )
            db.commit()

        shopify(Shopify(orders=[[order(1, sku="A")]]))
        assert connected.post(SYNC).status_code == 202

    def test_reclaiming_nothing_is_a_no_op(self, connected: TestClient) -> None:
        from app.services.sync import reclaim_interrupted_runs

        with session_module.get_session_factory()() as db:
            assert reclaim_interrupted_runs(db) == 0


class TestADuplicateOrderInOnePage:
    """A page write has to be idempotent in itself, not just against the database.

    The writer looked up existing orders once per page, so an order arriving
    twice before the flush looked new both times, inserted a second row, and died
    on `UNIQUE constraint failed: orders.workspace_id, orders.shopify_order_id`.

    Reproduced against the production store by two writers touching one run at
    once. It is also reachable from Shopify alone: cursor pagination over
    `created_at_min` is not a snapshot, so an order updated mid-walk shifts
    position and can be returned again.
    """

    def test_a_page_containing_the_same_order_twice_still_writes(
        self, connected: TestClient, shopify
    ) -> None:
        repeated = order(4242, sku="DD-1", quantity=3)
        shopify(Shopify(orders=[[repeated, repeated]]))

        connected.post(SYNC)

        stored = [o for o in rows(Order) if o.shopify_order_id == 4242]
        assert len(stored) == 1, "the duplicate was inserted a second time"
        run = rows(SyncRun)[-1]
        assert run.result == "success"
        assert run.error_code is None

    def test_the_repeat_is_not_counted_as_a_second_sale(
        self, connected: TestClient, shopify
    ) -> None:
        repeated = order(4243, sku="DD-1", quantity=2)
        shopify(Shopify(orders=[[repeated, repeated]]))

        connected.post(SYNC)

        run = rows(SyncRun)[-1]
        assert run.orders_synced == 1
        assert run.line_items_synced == 1
        # And the units are not doubled, which would inflate every sales figure.
        assert [li.quantity for li in rows(OrderLineItem)] == [2]

    def test_a_repeat_across_pages_still_writes_once(self, connected: TestClient, shopify) -> None:
        """The cross-page case, which committing per page already handled."""
        repeated = order(4244, sku="DD-1", quantity=1)
        shopify(Shopify(orders=[[repeated], [repeated]]))

        connected.post(SYNC)

        assert len([o for o in rows(Order) if o.shopify_order_id == 4244]) == 1
        assert rows(SyncRun)[-1].result == "success"

    def test_a_duplicate_line_item_within_a_page_writes_once(
        self, connected: TestClient, shopify
    ) -> None:
        payload = order(4245, sku="DD-1", quantity=1)
        payload["line_items"] = [payload["line_items"][0], payload["line_items"][0]]
        shopify(Shopify(orders=[[payload]]))

        connected.post(SYNC)

        assert len(rows(OrderLineItem)) == 1
        assert rows(SyncRun)[-1].result == "success"


class TestTheWindowASyncAsksFor:
    """A resumed cursor walks the window as it was when the cursor was issued.

    Shopify encodes ``created_at_min`` into the cursor, so orders created after it
    was minted were never in that sequence. On the production store every cursor
    carried a ``created_at_min`` from eight days earlier: the catch-up completed,
    cleared its cursor, and still left the newest 30 hours unfetched — and no
    amount of syncing would have reached the present.
    """

    def since_of(self, store: Shopify) -> str | None:
        """The window the run asked for.

        Read from any request that carries it: on a *resuming* run the cursor
        already encodes the filters, so only the count request spells them out.
        """
        for _url, params in store.requests:
            if "created_at_min" in params:
                return str(params["created_at_min"])
        return None

    def test_a_first_sync_asks_for_the_whole_lookback(self, connected: TestClient, shopify) -> None:
        store = shopify(Shopify(orders=[[order(1, sku="DD-1")]]))

        connected.post(SYNC)

        since = self.since_of(store)
        assert since is not None
        # 90 days back, give or take the second the test took.
        asked = datetime.fromisoformat(since)
        assert timedelta(days=89) < utcnow() - asked < timedelta(days=91)

    def test_a_later_sync_anchors_on_the_newest_order_it_holds(
        self, connected: TestClient, shopify
    ) -> None:
        """So catching up costs the new orders, not the whole window again."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", created_at="2026-07-20T10:00:00+00:00")]]))
        connected.post(SYNC)
        assert rows(SyncRun)[-1].result == "success"

        store = shopify(Shopify(orders=[[order(2, sku="DD-2")]]))
        connected.post(SYNC)

        since = self.since_of(store)
        assert since is not None
        asked = datetime.fromisoformat(since)
        # The newest order held, less the overlap — not 90 days ago.
        assert asked > datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
        assert asked < datetime(2026, 7, 20, 10, 1, tzinfo=UTC)

    def test_the_anchor_overlaps_rather_than_butting_up_exactly(
        self, connected: TestClient, shopify
    ) -> None:
        """Orders are not created in strictly increasing order in practice."""
        from app.services.sync import INCREMENTAL_OVERLAP

        created = "2026-07-20T10:00:00+00:00"
        shopify(Shopify(orders=[[order(1, sku="DD-1", created_at=created)]]))
        connected.post(SYNC)

        store = shopify(Shopify(orders=[[]]))
        connected.post(SYNC)

        asked = datetime.fromisoformat(str(self.since_of(store)))
        assert asked == datetime.fromisoformat(created) - INCREMENTAL_OVERLAP

    def test_a_resuming_run_keeps_the_full_lookback(self, connected: TestClient, shopify) -> None:
        """It is finishing a window it is already partway through."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", created_at="2026-07-20T10:00:00+00:00")]]))
        connected.post(SYNC)

        with session_module.get_session_factory()() as db:
            db.execute(text("UPDATE sync_runs SET cursor_orders = 'cursor-0', result = 'partial'"))
            db.commit()

        store = shopify(Shopify(orders=[[order(2, sku="DD-2")]]))
        connected.post(SYNC)

        asked = datetime.fromisoformat(str(self.since_of(store)))
        assert utcnow() - asked > timedelta(days=89)

    def test_after_a_partial_run_the_full_lookback_is_used(
        self, connected: TestClient, shopify
    ) -> None:
        """A gap left mid-window must be backfilled, not skipped over."""
        shopify(Shopify(orders=[[order(1, sku="DD-1")]], order_status=500))
        connected.post(SYNC)
        assert rows(SyncRun)[-1].result != "success"

        store = shopify(Shopify(orders=[[order(2, sku="DD-2")]]))
        connected.post(SYNC)

        asked = datetime.fromisoformat(str(self.since_of(store)))
        assert utcnow() - asked > timedelta(days=89)

    def test_the_anchor_never_reaches_past_the_lookback(
        self, connected: TestClient, shopify
    ) -> None:
        """An ancient newest-order must not widen the window beyond its bound."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", created_at="2020-01-01T00:00:00+00:00")]]))
        connected.post(SYNC)

        store = shopify(Shopify(orders=[[]]))
        connected.post(SYNC)

        asked = datetime.fromisoformat(str(self.since_of(store)))
        assert timedelta(days=89) < utcnow() - asked < timedelta(days=91)


class TestASyncFollowsEveryImport:
    """The "Sync now" button is gone from the routine path.

    An import restates which SKUs matter; their sales are only as current as the
    last pull from Shopify. Leaving that to a button meant a freshly imported
    workspace showing stale sales, or none, until someone thought to press it.
    """

    def upload(self, client: TestClient, body: str = "SKU,Total Qty.\nDD-1,10\n") -> dict:
        response = client.post(
            "/api/imports/upload",
            files={"file": ("stock.csv", io.BytesIO(body.encode()), "text/csv")},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_a_successful_import_starts_one(self, connected: TestClient, shopify) -> None:
        body = self.upload(connected)

        assert body["sync"]["started"] is True
        assert body["sync"]["run_id"] is not None
        assert body["sync"]["reason"] is None

    def test_the_run_is_recorded_as_import_triggered(self, connected: TestClient, shopify) -> None:
        """So Sync History distinguishes it from the Shopify page's button."""
        self.upload(connected)

        runs = rows(SyncRun)
        assert [r.trigger for r in runs] == ["import"]

    def test_it_pulls_orders_and_they_reach_the_figures(
        self, connected: TestClient, shopify
    ) -> None:
        """End to end: no button pressed, and Shopify Sales is populated."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))

        self.upload(connected)

        assert rows(Order)
        # A year, because the scripted order is dated 2026-07-01 and a 30-day
        # window from "today" may not reach it. What is under test is that the
        # sale arrived at all without anyone pressing anything.
        kpis = connected.get("/api/analytics/kpis?days=365").json()
        assert kpis["shopify_sales"] == 4

    def test_an_unconnected_workspace_still_imports(self, signed_in: TestClient) -> None:
        """A sheet is worth importing without a store, and the import must not
        fail because there is nothing to sync from."""
        body = self.upload(signed_in)

        assert body["batch"]["status"] == "complete"
        assert body["sync"] == {"started": False, "run_id": None, "reason": "not_connected"}
        assert rows(SyncRun) == []

    def test_a_failed_import_starts_nothing(self, connected: TestClient, shopify) -> None:
        """No rows landed, so there is nothing whose sales need catching up."""
        response = connected.post(
            "/api/imports/upload",
            files={"file": ("bad.csv", io.BytesIO(b"Nothing,Useful\n1,2\n"), "text/csv")},
        )

        assert response.status_code == 422
        assert rows(SyncRun) == []

    def test_a_second_import_does_not_queue_a_second_run(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A run already in flight covers these SKUs when it finishes, and
        `start_sync` would refuse a second anyway."""
        from app.services import sync as sync_service

        def already_running(*args: Any, **kwargs: Any):
            raise sync_service.SyncAlreadyRunningError

        monkeypatch.setattr(sync_service, "start_sync", already_running)
        body = self.upload(connected)

        assert body["sync"] == {"started": False, "run_id": None, "reason": "already_running"}

    def test_the_rollup_is_still_rebuilt_when_no_sync_runs(self, signed_in: TestClient) -> None:
        """A sync refreshes the rollup at its own end. Without one, the import
        has to do it, or the rows just written would never be aggregated."""
        self.upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-2,20\n")

        # The worker runs inline under the test settings, so this is settled.
        assert signed_in.get("/api/analytics/kpis?days=30").json()["total_skus"] == 2


class TestTheRollupIsPartOfTheRun:
    """A sync is not successful until the figures are recomputed from it.

    Orders in the database that the rollup has not seen are orders no figure on
    any screen reflects. The refresh used to run after the result was recorded
    and swallow its own failure, which left a green badge over a Dashboard
    showing yesterday's numbers, and a banner asking the user to press Recompute
    — a button for a repair the sync should have made itself.
    """

    def upload(self, client: TestClient) -> None:
        response = client.post(
            "/api/imports/upload",
            files={"file": ("stock.csv", io.BytesIO(b"SKU,Total Qty.\nDD-1,10\n"), "text/csv")},
        )
        assert response.status_code == 200, response.text

    def test_a_successful_sync_leaves_nothing_stale(self, connected: TestClient, shopify) -> None:
        """The property the banner reads. Nothing to recompute by hand."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        assert connected.get("/api/analytics/kpis?days=365").json()["stale"] is False

    def test_the_figures_are_current_the_moment_the_run_reports_success(
        self, connected: TestClient, shopify
    ) -> None:
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        run = rows(SyncRun)[-1]
        assert run.result == "success"
        assert connected.get("/api/analytics/kpis?days=365").json()["shopify_sales"] == 4

    def test_a_failed_recompute_is_not_a_successful_sync(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The orders landed, so this is partial rather than failed — and it is
        emphatically not success, which is what it used to be recorded as."""
        from sqlalchemy.exc import OperationalError

        from app.services import sync as sync_service

        def explode(*args: Any, **kwargs: Any):
            raise OperationalError("refresh", {}, Exception("disk I/O error"))

        monkeypatch.setattr(sync_service.metrics, "refresh_recent", explode)
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        run = rows(SyncRun)[-1]
        assert run.result == "partial"
        assert run.error_code == "rollup_failed"
        assert "recomputed" in (run.error_detail or "")

    def test_the_orders_are_kept_when_the_recompute_fails(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing needs re-fetching: the rows are already here."""
        from sqlalchemy.exc import OperationalError

        from app.services import sync as sync_service

        def explode(*args: Any, **kwargs: Any):
            raise OperationalError("refresh", {}, Exception("disk I/O error"))

        monkeypatch.setattr(sync_service.metrics, "refresh_recent", explode)
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        assert rows(Order)

    def test_only_a_real_failure_makes_the_figures_look_behind(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`stale` is what the banner reads, and it must mean exactly this."""
        from sqlalchemy.exc import OperationalError

        from app.services import sync as sync_service

        def explode(*args: Any, **kwargs: Any):
            raise OperationalError("refresh", {}, Exception("disk I/O error"))

        monkeypatch.setattr(sync_service.metrics, "refresh_recent", explode)
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        assert connected.get("/api/analytics/kpis?days=365").json()["stale"] is True

    def test_a_sync_that_found_nothing_new_recomputes_nothing(
        self, connected: TestClient, shopify
    ) -> None:
        """No orders arrived, so the rollup already covers everything there is.
        Rebuilding would be work with no result, and the run is still success."""
        shopify(Shopify(orders=[[]]))
        self.upload(connected)

        run = rows(SyncRun)[-1]
        assert run.orders_synced == 0
        assert run.result == "success"
        assert connected.get("/api/analytics/kpis?days=365").json()["stale"] is False

    def test_a_shopify_failure_is_reported_ahead_of_the_rollup(
        self, connected: TestClient, shopify
    ) -> None:
        """It is the earlier and more actionable of the two."""
        shopify(Shopify(order_status=500))
        self.upload(connected)

        run = rows(SyncRun)[-1]
        assert run.result in {"partial", "failed"}
        assert run.error_code != "rollup_failed"


class TestRetryRepeatsOnlyWhatFailed:
    """Retry Sync, and what it must not do.

    The requirement is precise: reuse the Shopify data already downloaded, and
    never ask for the import file again. Those are two different guarantees and
    both are asserted here.
    """

    def upload(self, client: TestClient) -> None:
        response = client.post(
            "/api/imports/upload",
            files={"file": ("stock.csv", io.BytesIO(b"SKU,Total Qty.\nDD-1,10\n"), "text/csv")},
        )
        assert response.status_code == 200, response.text

    def break_rollup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sqlalchemy.exc import OperationalError

        from app.services import sync as sync_service

        def explode(*args: Any, **kwargs: Any):
            raise OperationalError("refresh", {}, Exception("disk I/O error"))

        monkeypatch.setattr(sync_service.metrics, "refresh_recent", explode)

    def test_a_rollup_failure_retries_without_touching_shopify(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The orders are already here. Re-fetching them would be minutes of
        requests to arrive back exactly where we are."""
        store = shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.break_rollup(monkeypatch)
        self.upload(connected)
        assert rows(SyncRun)[-1].error_code == "rollup_failed"

        monkeypatch.undo()
        before = len(store.requests)
        response = connected.post("/api/shopify/sync/retry")

        assert response.status_code == 202
        assert len(store.requests) == before  # not one Shopify request

    def test_the_recompute_retry_repairs_the_figures(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.break_rollup(monkeypatch)
        self.upload(connected)
        assert connected.get("/api/analytics/kpis?days=365").json()["stale"] is True

        monkeypatch.undo()
        connected.post("/api/shopify/sync/retry")

        kpis = connected.get("/api/analytics/kpis?days=365").json()
        assert kpis["stale"] is False
        assert kpis["shopify_sales"] == 4

    def test_the_retry_run_reports_success(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.break_rollup(monkeypatch)
        self.upload(connected)

        monkeypatch.undo()
        connected.post("/api/shopify/sync/retry")

        run = rows(SyncRun)[-1]
        assert run.trigger == "retry"
        assert run.result == "success"

    def test_the_imported_sheet_is_never_touched_by_a_retry(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "Not require uploading the import file again" — the SKUs stand."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.break_rollup(monkeypatch)
        self.upload(connected)
        before = connected.get("/api/analytics/performance?days=365&limit=50").json()

        monkeypatch.undo()
        connected.post("/api/shopify/sync/retry")

        after = connected.get("/api/analytics/performance?days=365&limit=50").json()
        assert [r["sku"] for r in after["rows"]] == [r["sku"] for r in before["rows"]]
        assert after["total"] == before["total"] == 1

    def test_a_shopify_failure_retries_by_fetching_again(
        self, connected: TestClient, shopify
    ) -> None:
        """Nothing was downloaded, so there is nothing to reuse — and the run
        resumes from its cursor rather than restarting."""
        shopify(Shopify(order_status=500))
        self.upload(connected)
        assert rows(SyncRun)[-1].result in {"partial", "failed"}

        store = shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        before = len(store.requests)
        connected.post("/api/shopify/sync/retry")

        assert len(store.requests) > before  # it did go back to Shopify
        assert connected.get("/api/analytics/kpis?days=365").json()["shopify_sales"] == 4

    def test_retrying_needs_a_session(self, api: TestClient) -> None:
        assert api.post("/api/shopify/sync/retry").status_code == 401

    def test_retrying_without_a_store_is_refused(self, signed_in: TestClient) -> None:
        assert signed_in.post("/api/shopify/sync/retry").status_code == 404


class TestNothingLooksWrongWhileASyncRuns:
    """A sync in flight is not staleness, and must not read as it.

    A sync commits its orders page by page and recomputes at the end, so from
    the first page landing until the rollup runs there are always orders the
    rollup has not seen. Every successful sync passes through that state. Read
    as staleness it put "Sales figures are behind the last sync" on the
    Dashboard, beside a Retry button the server would have refused with 409.
    """

    def running_run(self, workspace_id: int = 1) -> None:
        """A run in flight, with a page of orders already committed."""
        with session_module.get_session_factory()() as db:
            connection = db.scalars(select(ShopifyConnection)).one()
            db.add(
                SyncRun(
                    workspace_id=workspace_id,
                    connection_id=connection.id,
                    trigger="import",
                    status="running",
                    stage="orders",
                    started_at=utcnow(),
                )
            )
            db.add(
                Order(
                    workspace_id=workspace_id,
                    connection_id=connection.id,
                    shopify_order_id=9001,
                    order_number="#9001",
                    currency="INR",
                    total_price_paise=100,
                    processed_at=utcnow(),
                    synced_at=utcnow(),
                )
            )
            db.commit()

    def test_stale_is_false_while_a_run_is_in_flight(self, connected: TestClient) -> None:
        self.running_run()

        assert connected.get("/api/analytics/kpis?days=365").json()["stale"] is False

    def test_syncing_says_what_is_actually_happening(self, connected: TestClient) -> None:
        self.running_run()

        assert connected.get("/api/analytics/kpis?days=365").json()["syncing"] is True

    def test_the_two_are_never_both_true(self, connected: TestClient) -> None:
        """The page shows one message or the other, so the server must not
        assert both at once."""
        self.running_run()
        kpis = connected.get("/api/analytics/kpis?days=365").json()

        assert not (kpis["stale"] and kpis["syncing"])

    def test_the_analytics_page_agrees_with_the_dashboard(self, connected: TestClient) -> None:
        self.running_run()
        kpis = connected.get("/api/analytics/kpis?days=365").json()
        insights = connected.get("/api/analytics/insights?days=365").json()

        assert (kpis["stale"], kpis["syncing"]) == (insights["stale"], insights["syncing"])

    def test_a_second_run_is_refused_while_one_is_in_flight(self, connected: TestClient) -> None:
        """What the hidden Retry button would have run into."""
        self.running_run()

        response = connected.post("/api/shopify/sync/retry")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "sync_already_running"

    def test_staleness_returns_once_the_run_is_over(self, connected: TestClient) -> None:
        """The guard is about runs in flight, not about hiding real failures."""
        self.running_run()
        with session_module.get_session_factory()() as db:
            run = db.scalars(select(SyncRun)).one()
            run.status = "finished"
            run.result = "partial"
            run.error_code = "rollup_failed"
            db.commit()

        assert connected.get("/api/analytics/kpis?days=365").json()["stale"] is True
        assert connected.get("/api/analytics/kpis?days=365").json()["syncing"] is False


class TestEveryStepIsRecorded:
    """Sync History keeps the account of how a run reached its result.

    A run row says what it ended up as. When one comes back `partial` that is
    one error string and no record of how far it got — which stage ran, what it
    managed, where it stopped.
    """

    def upload(self, client: TestClient) -> None:
        response = client.post(
            "/api/imports/upload",
            files={"file": ("stock.csv", io.BytesIO(b"SKU,Total Qty.\nDD-1,10\n"), "text/csv")},
        )
        assert response.status_code == 200, response.text

    def steps(self, client: TestClient) -> list[tuple[str, str]]:
        run_id = rows(SyncRun)[-1].id
        body = client.get(f"/api/shopify/syncs/{run_id}/steps").json()
        return [(s["step"], s["state"]) for s in body]

    def break_rollup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from sqlalchemy.exc import OperationalError

        from app.services import sync as sync_service

        def explode(*args: Any, **kwargs: Any):
            raise OperationalError("refresh", {}, Exception("disk I/O error"))

        monkeypatch.setattr(sync_service.metrics, "refresh_recent", explode)

    def test_the_whole_successful_workflow_is_logged_in_order(
        self, connected: TestClient, shopify
    ) -> None:
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        assert self.steps(connected) == [
            ("import_started", "ok"),
            ("inventory_imported", "ok"),
            ("sync_started", "started"),
            ("sync_completed", "ok"),
            ("recompute_started", "started"),
            ("recompute_completed", "ok"),
            ("workflow_finished", "ok"),
        ]

    def test_a_failed_recompute_is_logged_as_such(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The step that failed is named, not inferred from a final status."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.break_rollup(monkeypatch)
        self.upload(connected)

        steps = self.steps(connected)
        assert ("sync_completed", "ok") in steps
        assert ("recompute_failed", "failed") in steps
        assert ("recompute_completed", "ok") not in steps
        assert steps[-1] == ("workflow_finished", "failed")

    def test_a_failed_shopify_pull_is_logged_as_such(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(order_status=500))
        self.upload(connected)

        steps = self.steps(connected)
        assert ("sync_started", "started") in steps
        assert ("sync_completed", "failed") in steps
        # Nothing arrived, so there was nothing to recompute.
        assert not [s for s in steps if s[0].startswith("recompute")]

    def test_the_steps_carry_what_happened(self, connected: TestClient, shopify) -> None:
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        run_id = rows(SyncRun)[-1].id
        by_step = {
            s["step"]: s["detail"]
            for s in connected.get(f"/api/shopify/syncs/{run_id}/steps").json()
        }
        assert by_step["import_started"] == "stock.csv"
        assert "new" in (by_step["inventory_imported"] or "")
        assert "orders" in (by_step["sync_completed"] or "")
        assert "daily rows" in (by_step["recompute_completed"] or "")

    def test_the_import_and_its_sync_read_as_one_sequence(
        self, connected: TestClient, shopify
    ) -> None:
        """Both halves share the run, which is what makes the log readable."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.upload(connected)

        run_id = rows(SyncRun)[-1].id
        body = connected.get(f"/api/shopify/syncs/{run_id}/steps").json()
        assert len(body) == 7
        stamps = [s["at"] for s in body]
        assert stamps == sorted(stamps)

    def test_a_retry_logs_its_own_run(
        self, connected: TestClient, shopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry is a run of its own, so its steps are its own too."""
        shopify(Shopify(orders=[[order(1, sku="DD-1", quantity=4)]]))
        self.break_rollup(monkeypatch)
        self.upload(connected)
        first = rows(SyncRun)[-1].id

        monkeypatch.undo()
        connected.post("/api/shopify/sync/retry")
        second = rows(SyncRun)[-1].id

        assert second != first
        steps = [s["step"] for s in connected.get(f"/api/shopify/syncs/{second}/steps").json()]
        assert "recompute_completed" in steps
        # No import in this one — a retry never asks for the file again.
        assert "import_started" not in steps

    def test_the_steps_endpoint_needs_a_session(self, api: TestClient) -> None:
        assert api.get("/api/shopify/syncs/1/steps").status_code == 401

    def test_an_unknown_run_is_a_404(self, connected: TestClient) -> None:
        assert connected.get("/api/shopify/syncs/9999/steps").status_code == 404
