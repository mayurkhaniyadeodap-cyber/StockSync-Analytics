"""Analytics: the rollup, the six cards, and the SKU table.

Built on real rows through the real import endpoint, because the subject is how
the uploaded sheet and Shopify sales line up — a stubbed aggregate would assert
the fixture rather than the join.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, literal, select, text

from app.core import crypto
from app.db import session as session_module
from app.models import (
    COMPLAINT_COLUMNS,
    InventoryItem,
    Order,
    OrderLineItem,
    ShopifyConnection,
    SkuDailyComplaint,
    SkuDailyMetric,
    normalize_sku,
    utcnow,
)
from app.services import metrics as metrics_service

OVERVIEW = "/api/analytics/overview"
KPIS = "/api/analytics/kpis"
SKUS = "/api/analytics/skus"

#: The upload format, in the sheet's own order. There is no Quantity column —
#: "Total Qty." is the stock figure, exactly as the export carries it.
HEADERS = (
    "SKU,Total count.,Total Orders.,Total Qty.,"
    "Item Defect Partial,Item Defect Complete,Item Damage Partial,Item Damage Complete,"
    "Order Wrong Parcel,Electronics Item Nonworking Partial,"
    "Electronics Item Nonworking Complete,Missing,Missing Part,"
    "Item Mismatch Wrong Item Delivered"
)


def sheet_row(
    sku: str,
    total_qty: int,
    *,
    total_count: int = 0,
    total_orders: int = 0,
    complaints: tuple[int, ...] = (0,) * 10,
) -> str:
    """`total_qty` is the quantity — the sheet has no separate column for it."""
    values = [sku, total_count, total_orders, total_qty, *complaints]
    return ",".join(str(v) for v in values)


def import_sheet(client: TestClient, rows: list[str]) -> None:
    body = HEADERS + "\n" + "".join(f"{row}\n" for row in rows)
    response = client.post(
        "/api/imports/upload",
        files={"file": ("stock.csv", io.BytesIO(body.encode()), "text/csv")},
    )
    assert response.status_code == 200, response.text


def complain(
    client: TestClient,
    lines: list[tuple[str, tuple[int, ...], int]],
) -> None:
    """(sku, ten category counts, days ago) written as dated complaint rows.

    Writes ``sku_daily_complaints`` directly, the way ``sell`` writes orders
    directly: the table is what the importer produces from a raw complaint
    export, and building one through the upload endpoint would also restate the
    SKU's quantity and order counts, which these fixtures set from the sheet.

    The end-to-end path — a complaint CSV with a Date column becoming rows in
    this table — is covered in test_flexible_import.py, not faked here.
    """
    when = date.today()
    with session_module.get_session_factory()() as db:
        for sku, counts, days_ago in lines:
            by_category = {
                attribute: counts[index] for index, (attribute, _) in enumerate(COMPLAINT_COLUMNS)
            }
            db.add(
                SkuDailyComplaint(
                    workspace_id=1,
                    sku_normalized=normalize_sku(sku),
                    complaint_date=when - timedelta(days=days_ago),
                    total_complaints=sum(by_category.values()),
                    **by_category,
                )
            )
        db.commit()


def sell(
    client: TestClient,
    lines: list[tuple[str, int, int, int]],
    *,
    financial_status: str = "paid",
    cancelled: bool = False,
) -> None:
    """(sku, quantity, unit price paise, days ago) written as real orders."""
    now = utcnow()
    with session_module.get_session_factory()() as db:
        connection = db.scalars(select(ShopifyConnection)).first()
        if connection is None:
            connection = ShopifyConnection(
                workspace_id=1,
                shop_domain="s.myshopify.com",
                access_token_encrypted=crypto.encrypt(client.app.state.settings, "shpat_test"),
                status="connected",
                connected_at=now,
            )
            db.add(connection)
            db.flush()

        seed = db.scalar(select(Order).order_by(Order.id.desc()))
        next_id = (seed.shopify_order_id + 1) if seed else 40_000

        for offset, (sku, quantity, price, days_ago) in enumerate(lines):
            processed = datetime.now(UTC) - timedelta(days=days_ago)
            order = Order(
                workspace_id=1,
                connection_id=connection.id,
                shopify_order_id=next_id + offset,
                processed_at=processed,
                created_at_shopify=processed,
                financial_status=financial_status,
                cancelled_at=processed if cancelled else None,
                synced_at=now,
            )
            db.add(order)
            db.flush()
            db.add(
                OrderLineItem(
                    workspace_id=1,
                    order_id=order.id,
                    shopify_line_item_id=(next_id + offset) * 10,
                    sku_at_sale=sku,
                    sku_normalized=normalize_sku(sku),
                    quantity=quantity,
                    price_paise=price,
                )
            )
        db.commit()


def rebuild(client: TestClient) -> dict:
    return client.post("/api/analytics/rebuild").json()


@pytest.fixture
def shop(signed_in: TestClient) -> TestClient:
    """Three sheet SKUs with counts and complaints; two of them selling."""
    import_sheet(
        signed_in,
        [
            sheet_row(
                "DD-1001",
                100,
                total_count=120,
                total_orders=80,
                complaints=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
            ),
            sheet_row("DD-1002", 4, total_count=10, total_orders=6),
            sheet_row("DD-1003", 50),
        ],
    )
    sell(signed_in, [("DD-1001", 10, 19900, 1), ("DD-1001", 5, 19900, 3), ("DD-1002", 2, 89900, 2)])
    rebuild(signed_in)
    return signed_in


class TestAuthentication:
    def test_endpoints_require_a_session(self, api: TestClient) -> None:
        assert api.get(OVERVIEW).status_code == 401
        assert api.get(KPIS).status_code == 401
        assert api.get(SKUS).status_code == 401
        assert api.post("/api/analytics/rebuild").status_code == 401


class TestRollup:
    def test_rebuild_writes_one_row_per_sku_per_day(self, signed_in: TestClient) -> None:
        import_sheet(signed_in, [sheet_row("DD-1001", 10)])
        sell(signed_in, [("DD-1001", 3, 19900, 1), ("DD-1001", 2, 19900, 1)])

        result = rebuild(signed_in)

        assert result["rows_written"] == 1  # two orders, same SKU, same day
        with session_module.get_session_factory()() as db:
            row = db.scalars(select(SkuDailyMetric)).one()
            assert row.units_sold == 5

    def test_cancelled_orders_are_excluded(self, signed_in: TestClient) -> None:
        import_sheet(signed_in, [sheet_row("DD-1001", 10)])
        sell(signed_in, [("DD-1001", 5, 19900, 1)], cancelled=True)

        rebuild(signed_in)

        assert signed_in.get(KPIS).json()["shopify_sales"] == 0

    def test_refunded_orders_are_excluded(self, signed_in: TestClient) -> None:
        import_sheet(signed_in, [sheet_row("DD-1001", 10)])
        sell(signed_in, [("DD-1001", 5, 19900, 1)], financial_status="refunded")

        rebuild(signed_in)

        assert signed_in.get(KPIS).json()["shopify_sales"] == 0

    def test_rebuilding_is_idempotent(self, shop: TestClient) -> None:
        """Delete-then-insert, so a second run must not double the numbers."""
        first = shop.get(KPIS).json()["shopify_sales"]

        rebuild(shop)

        assert shop.get(KPIS).json()["shopify_sales"] == first

    def test_the_rollup_is_reported_stale_when_orders_are_newer(self, shop: TestClient) -> None:
        assert shop.get(KPIS).json()["stale"] is False

        sell(shop, [("DD-1001", 1, 19900, 0)])

        assert shop.get(KPIS).json()["stale"] is True


class TestTheStagedRebuild:
    """The rebuild computes into a temporary table and swaps once.

    The point of the staging is that the expensive aggregation runs without
    SQLite's write lock; what these tests can assert cheaply is the part that
    broke while getting there — the temporary table's lifecycle, and that the
    swap is committed and whole by the time `refresh` returns.
    """

    def test_a_rebuild_is_committed_by_the_time_it_returns(self, shop: TestClient) -> None:
        """Another session sees the new rollup immediately, not on the next commit.

        The swap runs on its own pinned connection now, so this is worth
        stating: `refresh` returning means the rows are durable and visible,
        not merely pending on the caller's session.
        """
        with session_module.get_session_factory()() as db:
            db.execute(delete(SkuDailyMetric))
            db.commit()

        with session_module.get_session_factory()() as writer:
            result = metrics_service.refresh(writer, workspace_id=1)

        with session_module.get_session_factory()() as reader:
            visible = reader.scalar(select(func.count()).select_from(SkuDailyMetric))
        assert result.rows_written > 0
        assert visible == result.rows_written

    def test_consecutive_rebuilds_agree(self, shop: TestClient) -> None:
        """Three in a row, same answer.

        The connection is returned to the pool between them carrying its
        temporary database, so the second and third rebuild are the ones that
        meet a staging table left by an earlier run.
        """
        answers = []
        for _ in range(3):
            with session_module.get_session_factory()() as db:
                answers.append(metrics_service.refresh(db, workspace_id=1).rows_written)

        assert len(set(answers)) == 1

    def test_staging_replaces_a_table_left_by_an_earlier_run(self) -> None:
        """A stale staging table is dropped, not appended to or joined against.

        Deliberately created with the wrong shape: if `_stage` ever stopped
        dropping first, this is the failure — an insert against a table that
        does not have the columns, or worse, one that does and still holds the
        previous rebuild's rows.
        """
        aggregation = select(
            literal("DD-STALE").label("sku_normalized"),
            literal("2026-01-01").label("metric_date"),
            literal(7).label("units_sold"),
            literal(700).label("revenue_paise"),
            literal(1).label("order_count"),
        )

        with session_module.get_engine().connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS temp.metrics_staging"))
            conn.execute(text("CREATE TEMP TABLE metrics_staging (wrong_column INTEGER)"))

            metrics_service._stage(conn, aggregation)

            staged = conn.execute(text("SELECT * FROM temp.metrics_staging")).all()
            assert staged == [("DD-STALE", "2026-01-01", 7, 700, 1)]

            conn.execute(text("DROP TABLE IF EXISTS temp.metrics_staging"))
            conn.commit()


class TestTheSixCards:
    def test_every_card(self, shop: TestClient) -> None:
        body = shop.get(KPIS).json()

        assert body["total_skus"] == 3
        assert body["total_quantity"] == 154  # 100 + 4 + 50
        assert body["total_orders"] == 86  # 80 + 6 + 0, from the sheet
        assert body["total_complaints"] == 55  # 1..10 on the first SKU
        assert body["shopify_sales"] == 17  # 10 + 5 + 2
        assert body["shopify_sales_pct"] == 100.0  # every selling SKU is in the sheet

    def test_total_orders_comes_from_the_sheet_not_from_shopify(self, shop: TestClient) -> None:
        """The sheet is the source of truth; Shopify supplies units only."""
        body = shop.get(KPIS).json()

        # Three Shopify orders were written, but the sheet says 86.
        assert body["total_orders"] == 86

    def test_total_complaints_sums_every_category(self, signed_in: TestClient) -> None:
        import_sheet(
            signed_in,
            [
                sheet_row("DD-1", 1, complaints=(1,) * 10),
                sheet_row("DD-2", 1, complaints=(2,) * 10),
            ],
        )

        assert signed_in.get(KPIS).json()["total_complaints"] == 30

    def test_an_unmatched_sku_dilutes_the_share(self, signed_in: TestClient) -> None:
        """A SKU the store sold but the sheet has never heard of is part of what
        was sold, so it belongs in the denominator."""
        import_sheet(signed_in, [sheet_row("DD-1", 10)])
        sell(signed_in, [("DD-1", 25, 1000, 1), ("NOT-IN-SHEET", 75, 1000, 1)])
        rebuild(signed_in)

        body = signed_in.get(KPIS).json()

        assert body["shopify_sales"] == 25
        assert body["shopify_sales_all"] == 100
        assert body["shopify_sales_pct"] == 25.0

    def test_the_percentage_is_bounded(self, signed_in: TestClient) -> None:
        """The old sell-through read 8,512% on real data. A share cannot."""
        import_sheet(signed_in, [sheet_row("DD-1", 1)])
        sell(signed_in, [("DD-1", 5000, 1000, 1)])
        rebuild(signed_in)

        assert signed_in.get(KPIS).json()["shopify_sales_pct"] == 100.0

    def test_no_sales_does_not_divide_by_zero(self, signed_in: TestClient) -> None:
        import_sheet(signed_in, [sheet_row("DD-1", 10)])

        assert signed_in.get(KPIS).json()["shopify_sales_pct"] == 0.0

    def test_a_narrower_window_counts_fewer_sales(self, shop: TestClient) -> None:
        wide = shop.get(KPIS, params={"days": 30}).json()["shopify_sales"]
        narrow = shop.get(KPIS, params={"days": 2}).json()["shopify_sales"]

        assert narrow < wide

    def test_the_sheet_figures_do_not_move_with_the_window(self, shop: TestClient) -> None:
        """Quantity and complaints are current state, not a windowed measure."""
        wide = shop.get(KPIS, params={"days": 90}).json()
        narrow = shop.get(KPIS, params={"days": 7}).json()

        assert wide["total_quantity"] == narrow["total_quantity"]
        assert wide["total_complaints"] == narrow["total_complaints"]
        assert wide["total_orders"] == narrow["total_orders"]


class TestTheSkuTable:
    def test_a_row_carries_the_sheet_and_shopify_together(self, shop: TestClient) -> None:
        body = shop.get(SKUS).json()

        row = next(r for r in body["rows"] if r["sku"] == "DD-1001")
        assert row["quantity"] == 100
        assert row["total_orders"] == 80
        # Quantity and Total Qty are the same cell: the sheet has one figure.
        assert row["total_qty"] == 100
        assert row["total_count"] == 120
        assert row["shopify_sales"] == 15
        assert row["total_complaints"] == 55

    def test_every_complaint_category_is_its_own_value(self, shop: TestClient) -> None:
        row = next(r for r in shop.get(SKUS).json()["rows"] if r["sku"] == "DD-1001")

        assert row["complaints"] == {
            "item_defect_partial": 1,
            "item_defect_complete": 2,
            "item_damage_partial": 3,
            "item_damage_complete": 4,
            "order_wrong_parcel": 5,
            "electronics_nonworking_partial": 6,
            "electronics_nonworking_complete": 7,
            "missing": 8,
            "missing_part": 9,
            "item_mismatch_wrong_item": 10,
        }

    def test_the_column_set_travels_with_the_page(self, shop: TestClient) -> None:
        """So the client's headers and its cells cannot disagree."""
        body = shop.get(SKUS).json()

        assert [c["field"] for c in body["complaint_columns"]] == [
            attribute for attribute, _ in COMPLAINT_COLUMNS
        ]
        assert body["complaint_columns"][0]["header"] == "Item Defect Partial"

    def test_shopify_sales_pct_is_the_skus_share_of_the_sheet(self, shop: TestClient) -> None:
        """15 of the 17 units the imported SKUs sold. Everything this fixture
        sells is in the sheet, so the store's total happens to be 17 as well —
        the two denominators are separated in
        `TestTheShareTheTableShowsAndTheShareTheCardShows`."""
        row = next(r for r in shop.get(SKUS).json()["rows"] if r["sku"] == "DD-1001")

        assert row["shopify_sales_pct"] == pytest.approx(15 / 17 * 100, abs=0.1)

    def test_a_sku_with_no_shopify_sales_still_appears(self, shop: TestClient) -> None:
        """The sheet is the source of truth: its rows exist regardless."""
        row = next(r for r in shop.get(SKUS).json()["rows"] if r["sku"] == "DD-1003")

        assert row["shopify_sales"] == 0
        assert row["quantity"] == 50

    def test_matching_is_by_sku_alone_and_normalised(self, signed_in: TestClient) -> None:
        """The sheet writes "DD 1001"; Shopify sold "dd-1001"."""
        import_sheet(signed_in, [sheet_row("DD 1001", 10)])
        sell(signed_in, [("dd-1001", 7, 1000, 1)])
        rebuild(signed_in)

        row = signed_in.get(SKUS).json()["rows"][0]

        assert row["sku"] == "DD 1001"
        assert row["shopify_sales"] == 7

    def test_searching_by_sku(self, shop: TestClient) -> None:
        body = shop.get(SKUS, params={"search": "1002"}).json()

        assert body["total"] == 1
        assert body["rows"][0]["sku"] == "DD-1002"

    def test_pagination(self, shop: TestClient) -> None:
        body = shop.get(SKUS, params={"limit": 2}).json()

        assert body["total"] == 3
        assert len(body["rows"]) == 2

    def test_the_stock_badge_uses_the_workspace_threshold(self, shop: TestClient) -> None:
        rows = {r["sku"]: r["stock_status"] for r in shop.get(SKUS).json()["rows"]}

        assert rows["DD-1001"] == "in"  # 100, above the threshold of 10
        assert rows["DD-1002"] == "low"  # 4

    def test_rows_are_ordered_by_shopify_sales(self, shop: TestClient) -> None:
        sales = [r["shopify_sales"] for r in shop.get(SKUS).json()["rows"]]

        assert sales == sorted(sales, reverse=True)


class TestOverview:
    def test_one_call_returns_the_cards_and_the_trend(self, shop: TestClient) -> None:
        body = shop.get(OVERVIEW).json()

        assert body["has_data"] is True
        assert body["kpis"]["total_skus"] == 3
        assert body["trend"]["points"]

    def test_an_empty_workspace_reports_no_data(self, signed_in: TestClient) -> None:
        body = signed_in.get(OVERVIEW).json()

        assert body["has_data"] is False
        assert body["kpis"]["total_skus"] == 0


class TestSkuTableOrdering:
    """Highest sales first; ties alphabetically by the SKU as written."""

    def test_equal_sales_sort_alphabetically_by_sku(self, signed_in: TestClient) -> None:
        """
        `sku_normalized` was the old tiebreak and is not alphabetical by the
        displayed code: it drops separators, so `DD-10` and `DD-2` ordered by
        `dd10`/`dd2` rather than by what the table shows.
        """
        import_sheet(signed_in, [sheet_row("DD-2", 1), sheet_row("DD-10", 1), sheet_row("AA-3", 1)])

        rows = signed_in.get("/api/analytics/skus").json()["rows"]

        # None of these sold, so every tiebreak applies and nothing else does.
        assert [row["sku"] for row in rows] == ["AA-3", "DD-10", "DD-2"]

    def test_case_does_not_split_the_alphabet(self, signed_in: TestClient) -> None:
        import_sheet(signed_in, [sheet_row("bb-1", 1), sheet_row("AA-1", 1), sheet_row("CC-1", 1)])

        rows = signed_in.get("/api/analytics/skus").json()["rows"]

        assert [row["sku"] for row in rows] == ["AA-1", "bb-1", "CC-1"]


class TestOneDefinitionOfEveryFigure:
    """The same number must read the same wherever it is shown."""

    def test_sales_pct_agrees_across_dashboard_analytics_and_import(self, shop: TestClient) -> None:
        """
        Four modules computed this and two rounded differently, so the same
        share rendered 42.2 on the Dashboard and 42.19 on the import summary.
        They now share one function.
        """
        # The import first, and the two reads after it. An import now replaces
        # the dataset, so reading either endpoint beforehand would compare three
        # figures over two different sets of SKUs — which is a different bug
        # from the rounding one this test exists for.
        after_import = import_sheet_result(shop)["analysis"]["shopify_sales_pct"]
        dashboard = shop.get(KPIS).json()["shopify_sales_pct"]
        analytics_page = shop.get("/api/analytics/insights").json()["kpis"]["shopify_sales_pct"]

        assert dashboard == analytics_page == after_import

    def test_sales_pct_uses_the_shared_helper(self) -> None:
        from app.core.calc import sales_pct

        assert sales_pct(570435, 1352080) == 42.19
        assert sales_pct(1, 3) == 33.33  # two decimals, everywhere
        assert sales_pct(5, 0) == 0.0  # no whole -> zero, not a division

    def test_total_quantity_agrees_between_dashboard_and_analytics(self, shop: TestClient) -> None:
        """
        The Dashboard summed `quantity_on_hand` and Analytics summed `total_qty`,
        which differed by 28,280 units on the live workspace. Both now read the
        one field.
        """
        dashboard = shop.get(KPIS).json()["total_quantity"]
        analytics_page = shop.get("/api/analytics/insights").json()["kpis"]["total_qty"]

        assert dashboard == analytics_page

    def test_the_sku_table_quantity_matches_the_card(self, shop: TestClient) -> None:
        rows = shop.get(SKUS).json()["rows"]
        total = sum(row["quantity"] for row in rows)

        assert total == shop.get(KPIS).json()["total_quantity"]

    def test_quantity_and_total_qty_are_the_same_column(self, shop: TestClient) -> None:
        for row in shop.get(SKUS).json()["rows"]:
            assert row["quantity"] == row["total_qty"], row["sku"]


class TestTheShareTheTableShowsAndTheShareTheCardShows:
    """Two denominators, on purpose, and they must not be swapped.

    The `shop` fixture cannot tell them apart — everything it sells is in the
    sheet, so the store total and the sheet's total are the same 17 units. This
    one sells a SKU the sheet has never heard of, which separates them.
    """

    @pytest.fixture
    def wider(self, signed_in: TestClient) -> TestClient:
        import_sheet(signed_in, [sheet_row("DD-1001", 100), sheet_row("DD-1002", 100)])
        sell(
            signed_in,
            [("DD-1001", 30, 19900, 1), ("DD-1002", 10, 19900, 2), ("DD-9999", 60, 19900, 3)],
        )
        rebuild(signed_in)
        return signed_in

    def test_the_card_divides_by_everything_the_store_sold(self, wider: TestClient) -> None:
        card = wider.get(KPIS).json()

        assert card["shopify_sales"] == 40  # 30 + 10; DD-9999 is not in the sheet
        assert card["shopify_sales_all"] == 100  # …but it is in the denominator
        assert card["shopify_sales_pct"] == 40.0

    def test_the_table_divides_by_the_imported_skus_own_sales(self, wider: TestClient) -> None:
        rows = {r["sku"]: r for r in wider.get(SKUS).json()["rows"]}

        assert rows["DD-1001"]["shopify_sales_pct"] == 75.0  # 30 of the sheet's 40, not of 100
        assert rows["DD-1002"]["shopify_sales_pct"] == 25.0

    def test_the_table_column_sums_to_one_hundred(self, wider: TestClient) -> None:
        rows = wider.get(SKUS).json()["rows"]

        assert sum(r["shopify_sales_pct"] for r in rows) == 100.0
        assert all(r["shopify_sales_pct"] <= 100 for r in rows)

    def test_the_dashboard_table_and_the_performance_page_agree(self, wider: TestClient) -> None:
        """They are the same table with the same columns; one number each."""
        dashboard = {r["sku"]: r["shopify_sales_pct"] for r in wider.get(SKUS).json()["rows"]}
        page = {
            r["sku"]: r["shopify_sales_pct"]
            for r in wider.get("/api/analytics/performance?limit=200").json()["rows"]
        }

        assert dashboard == page

    def test_searching_the_table_does_not_move_the_denominator(self, wider: TestClient) -> None:
        rows = wider.get(f"{SKUS}?search=DD-1001").json()["rows"]

        assert [r["sku"] for r in rows] == ["DD-1001"]
        assert rows[0]["shopify_sales_pct"] == 75.0  # not 100% of itself


def import_sheet_result(client: TestClient) -> dict:
    """Re-import the same sheet and return the response, for cross-checking."""
    body = HEADERS + "\n" + sheet_row("DD-1001", 100, total_count=120, total_orders=80) + "\n"
    response = client.post(
        "/api/imports/upload",
        files={"file": ("stock.csv", io.BytesIO(body.encode()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def make_legacy(sku: str, *, on_hand: int, total_qty: int) -> None:
    """Force the two quantity columns apart, as older imports left them.

    The importer reconciles them, so no fixture built through the API can
    produce this — which is exactly why the inconsistency survived three rounds
    of tests and was only visible on the live database.
    """
    with session_module.get_session_factory()() as db:
        item = db.scalars(
            select(InventoryItem).where(InventoryItem.sku_normalized == normalize_sku(sku))
        ).one()
        item.quantity_on_hand = on_hand
        item.total_qty = total_qty
        db.commit()


@pytest.fixture
def legacy(signed_in: TestClient) -> TestClient:
    """Two SKUs whose stored columns disagree, as batch 10 left 213 of them."""
    import_sheet(signed_in, [sheet_row("DD-1001", 100), sheet_row("DD-1002", 50)])
    make_legacy("DD-1001", on_hand=10, total_qty=100)
    make_legacy("DD-1002", on_hand=5, total_qty=50)
    return signed_in


class TestEveryScreenReadsTotalQty:
    """One field, everywhere a quantity is shown.

    ``quantity_on_hand`` and ``total_qty`` are written from one cell, so they
    agree for anything imported today. They disagreed on 213 live rows written
    by an older parser, and because different screens summed different columns
    the workspace reported 72,607 units in one place and 44,327 in another.
    """

    def test_the_dashboard_card_sums_total_qty(self, legacy: TestClient) -> None:
        assert legacy.get(KPIS).json()["total_quantity"] == 150  # not 15

    def test_the_sku_table_column_matches_the_card(self, legacy: TestClient) -> None:
        rows = legacy.get(SKUS).json()["rows"]

        assert sum(row["quantity"] for row in rows) == legacy.get(KPIS).json()["total_quantity"]

    def test_the_table_quantity_equals_its_own_total_qty(self, legacy: TestClient) -> None:
        for row in legacy.get(SKUS).json()["rows"]:
            assert row["quantity"] == row["total_qty"], row["sku"]

    def test_the_stock_badge_is_drawn_at_the_same_line_as_the_card(
        self, legacy: TestClient
    ) -> None:
        """
        The badge read `quantity_on_hand` while Low stock counted `total_qty`,
        so a SKU could be badged "Low" while the card did not count it.
        """
        rows = legacy.get(SKUS).json()["rows"]
        threshold = legacy.get(KPIS).json()["low_stock_threshold"]
        badged_low = sum(1 for row in rows if row["stock_status"] == "low")

        assert badged_low == sum(1 for row in rows if 0 < row["total_qty"] <= threshold)

    def test_the_insights_read_the_same_field(self, legacy: TestClient) -> None:
        insights = legacy.get("/api/analytics/insights").json()

        assert insights["kpis"]["total_qty"] == legacy.get(KPIS).json()["total_quantity"]

    def test_sku_performance_reports_total_qty(self, legacy: TestClient) -> None:
        rows = legacy.get("/api/analytics/performance").json()["rows"]

        assert sum(row["total_qty"] for row in rows) == 150

    def test_the_import_summary_agrees(self, legacy: TestClient) -> None:
        """The Import page's own total, beside the dashboard's."""
        assert legacy.get("/api/inventory/summary").json()["total_quantity"] == 150

    def test_the_reports_agree(self, legacy: TestClient) -> None:
        preview = legacy.get("/api/reports/preview", params={"kind": "inventory"}).json()
        headers = [column["header"] for column in preview["columns"]]
        quantity = headers.index("Quantity")
        total_qty = headers.index("Total Qty")

        for row in preview["rows"]:
            assert row[quantity] == row[total_qty], row[0]

    def test_every_surface_reports_the_same_number(self, legacy: TestClient) -> None:
        """The whole point, asserted in one place."""
        card = legacy.get(KPIS).json()["total_quantity"]
        table = sum(row["quantity"] for row in legacy.get(SKUS).json()["rows"])
        insights = legacy.get("/api/analytics/insights").json()["kpis"]["total_qty"]
        performance = sum(
            row["total_qty"] for row in legacy.get("/api/analytics/performance").json()["rows"]
        )
        summary = legacy.get("/api/inventory/summary").json()["total_quantity"]

        assert card == table == insights == performance == summary == 150
