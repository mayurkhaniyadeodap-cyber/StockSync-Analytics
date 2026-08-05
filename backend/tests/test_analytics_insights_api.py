"""The Analytics page endpoints, end to end.

Built on real imports and real orders through the real endpoints, reusing
test_analytics_api's helpers — the subject is whether the sheet and Shopify line
up through the whole stack, which a stubbed aggregate could not tell us.

The derivations themselves are covered in test_insights.py against literal rows.
What is asserted here is the wiring: that the numbers survive the join, the
schema and the query string.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import COMPLAINT_COLUMNS
from tests.test_analytics_api import complain, import_sheet, rebuild, sell, sheet_row

INSIGHTS = "/api/analytics/insights"
PERFORMANCE = "/api/analytics/performance"

DEFECT = COMPLAINT_COLUMNS[0][0]


@pytest.fixture
def store(signed_in: TestClient) -> TestClient:
    """Four SKUs chosen so every panel on the page has something to show.

    Status is decided by the complaint *count* — critical at 25, attention at
    8 — since Complaint Rate % was removed from the project.

    * DD-1001 — 100 held, 1 complaint, sells hard
    * DD-1002 — 40 held, 20 complaints (attention)
    * DD-1003 — 900 held, never sold (attention: dead stock)
    * DD-1004 — 10 held, 12 complaints (attention)
    """
    import_sheet(
        signed_in,
        [
            sheet_row("DD-1001", 100, total_orders=200, complaints=(1, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
            sheet_row("DD-1002", 40, total_orders=40, complaints=(20, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
            sheet_row("DD-1003", 900, total_orders=0),
            sheet_row("DD-1004", 10, total_orders=100, complaints=(0, 0, 12, 0, 0, 0, 0, 0, 0, 0)),
        ],
    )
    # The same complaint tallies the sheet carries, now with dates on them, so
    # every window that covers the orders also covers the complaints. Without
    # dates these SKUs would report zero complaints in every range — which is
    # exactly what an aggregated-only import does, covered separately.
    complain(
        signed_in,
        [
            ("DD-1001", (1, 0, 0, 0, 0, 0, 0, 0, 0, 0), 1),
            ("DD-1002", (20, 0, 0, 0, 0, 0, 0, 0, 0, 0), 1),
            ("DD-1004", (0, 0, 12, 0, 0, 0, 0, 0, 0, 0), 1),
        ],
    )
    sell(
        signed_in,
        [
            ("DD-1001", 300, 19900, 1),
            ("DD-1002", 20, 19900, 2),
            ("DD-1004", 5, 19900, 3),
            # A SKU the store sold that the sheet has never heard of. It belongs
            # in the denominator of Shopify Sales %, and nowhere else.
            ("DD-9999", 100, 19900, 1),
        ],
    )
    rebuild(signed_in)
    return signed_in


class TestAuthentication:
    def test_both_endpoints_require_a_session(self, api: TestClient) -> None:
        assert api.get(INSIGHTS).status_code == 401
        assert api.get(PERFORMANCE).status_code == 401


class TestInsights:
    def test_the_eight_kpis_come_from_real_rows(self, store: TestClient) -> None:
        k = store.get(INSIGHTS).json()["kpis"]

        assert k["total_skus"] == 4
        assert k["total_qty"] == 1050  # 100 + 40 + 900 + 10
        assert k["shopify_sales"] == 325  # 300 + 20 + 5; DD-9999 is not in the sheet
        assert k["shopify_sales_all"] == 425  # …but it is in the denominator
        assert k["shopify_sales_pct"] == 76.47  # 325 of the store's 425
        assert k["total_orders"] == 340
        assert k["total_complaints"] == 33
        assert k["avg_sales_per_sku"] == 81.2  # 325 / 4

    def test_an_unmatched_shopify_sku_never_becomes_a_row(self, store: TestClient) -> None:
        """Shopify is a sales source, not a source of SKUs."""
        body = store.get(INSIGHTS).json()

        skus = {row["sku"] for row in body["rankings"]["top_selling"]}
        assert "DD-9999" not in skus
        assert body["kpis"]["total_skus"] == 4

    def test_sales_names_the_extremes(self, store: TestClient) -> None:
        sales = store.get(INSIGHTS).json()["sales"]

        assert sales["highest"]["sku"] == "DD-1001"
        assert sales["highest"]["rank"] == 1
        assert sales["lowest"]["sku"] == "DD-1003"  # sold nothing
        assert sales["shopify_sales"] == 325

    def test_the_distribution_accounts_for_every_matched_unit(self, store: TestClient) -> None:
        sales = store.get(INSIGHTS).json()["sales"]

        assert sum(slice_["count"] for slice_ in sales["distribution"]) == 325

    def test_complaints_are_totalled_and_categorised(self, store: TestClient) -> None:
        c = store.get(INSIGHTS).json()["complaints"]

        assert c["total_complaints"] == 33
        assert c["most_complained"]["sku"] == "DD-1002"  # 20, the largest count
        assert c["skus_with_complaints"] == 3
        assert len(c["categories"]) == len(COMPLAINT_COLUMNS)
        assert c["categories"][0]["field_name"] == DEFECT  # 21 of the 33

    def test_the_three_rankings_are_present_and_ordered(self, store: TestClient) -> None:
        r = store.get(INSIGHTS).json()["rankings"]

        assert [row["sku"] for row in r["top_selling"]] == ["DD-1001", "DD-1002", "DD-1004"]
        assert r["lowest_selling"][0]["sku"] == "DD-1003"
        # By complaint count: 20, then 12, then 1.
        assert [row["sku"] for row in r["highest_complaint"]] == [
            "DD-1002",
            "DD-1004",
            "DD-1001",
        ]

    def test_inventory_insights_state_the_cut_they_made(self, store: TestClient) -> None:
        i = store.get(INSIGHTS).json()["inventory"]

        assert i["median_qty"] == 70.0  # (40 + 100) / 2
        assert i["median_sales"] == 12.5  # (5 + 20) / 2
        assert [row["sku"] for row in i["zero_sales"]] == ["DD-1003"]
        assert i["zero_sales_total"] == 1
        assert [row["sku"] for row in i["high_stock_low_sales"]] == ["DD-1003"]
        # DD-1002 holds under the median (40 <= 70) and sells over it (20 > 12.5).
        # DD-1001 sells more but is not thin on stock, so it is not this finding.
        assert [row["sku"] for row in i["low_stock_high_sales"]] == ["DD-1002"]

    def test_quick_insights_are_generated_from_the_data(self, store: TestClient) -> None:
        quick = store.get(INSIGHTS).json()["quick"]

        found = {card["key"]: card for card in quick}
        assert found["complaints"]["sku"] == "DD-1002"
        assert found["nosales"]["value"] == "1 SKUs"
        assert "DD-1003" in found["nosales"]["note"]
        # Every card names a SKU and carries a value; none is a placeholder.
        assert all(card["value"] for card in quick)

    def test_the_trend_comes_back_with_the_insights(self, store: TestClient) -> None:
        body = store.get(INSIGHTS).json()

        assert body["trend"]["days"] == 30
        assert len(body["trend"]["points"]) == 30
        assert sum(point["units"] for point in body["trend"]["points"]) == 425

    def test_complaint_columns_travel_with_the_payload(self, store: TestClient) -> None:
        """So the client's headers and its cells cannot disagree about the set."""
        body = store.get(INSIGHTS).json()

        assert [c["field"] for c in body["complaint_columns"]] == [f for f, _ in COMPLAINT_COLUMNS]

    def test_an_empty_workspace_says_so_rather_than_failing(self, signed_in: TestClient) -> None:
        body = signed_in.get(INSIGHTS).json()

        assert body["has_data"] is False
        assert body["kpis"]["total_skus"] == 0
        assert body["quick"] == []
        assert body["sales"]["highest"] is None
        assert body["complaints"]["most_complained"] is None
        assert body["rankings"]["top_selling"] == []

    def test_a_sheet_with_no_shopify_data_still_reports_the_sheet(
        self, signed_in: TestClient
    ) -> None:
        """Nothing synced: inventory figures stand, sales figures are zero."""
        import_sheet(signed_in, [sheet_row("DD-1", 10, total_orders=5)])

        body = signed_in.get(INSIGHTS).json()

        assert body["has_data"] is True
        assert body["kpis"]["total_qty"] == 10
        assert body["kpis"]["shopify_sales"] == 0
        assert body["kpis"]["shopify_sales_pct"] == 0.0
        assert body["sales"]["highest"] is None

    def test_the_range_narrows_the_sales_not_the_sheet(self, store: TestClient) -> None:
        """Stock is a current figure; sales belong to a window."""
        # Two days: today and yesterday. DD-1001 sold yesterday, DD-1002 the day
        # before that, so narrowing drops one and keeps the other.
        narrow = store.get(f"{INSIGHTS}?days=2").json()

        assert narrow["days"] == 2
        assert narrow["kpis"]["total_qty"] == 1050  # stock is current, not windowed
        assert narrow["kpis"]["shopify_sales"] == 300
        assert store.get(f"{INSIGHTS}?days=1").json()["kpis"]["shopify_sales"] == 0

    def test_an_out_of_range_window_is_refused(self, store: TestClient) -> None:
        assert store.get(f"{INSIGHTS}?days=0").status_code == 422
        assert store.get(f"{INSIGHTS}?days=9999").status_code == 422


class TestPerformanceTable:
    def test_every_sku_with_a_computed_status(self, store: TestClient) -> None:
        body = store.get(PERFORMANCE).json()

        assert body["total"] == 4
        by_sku = {row["sku"]: row for row in body["rows"]}
        # Counts, against 25 for critical and 8 for attention.
        assert by_sku["DD-1002"]["status"] == "attention"  # 20 complaints
        assert by_sku["DD-1004"]["status"] == "attention"  # 12
        assert by_sku["DD-1003"]["status"] == "attention"  # stock, no sales
        assert by_sku["DD-1001"]["status"] == "excellent"  # 1, and it sells

    def test_the_columns_the_page_asks_for(self, store: TestClient) -> None:
        row = store.get(f"{PERFORMANCE}?search=DD-1002").json()["rows"][0]

        assert row["sku"] == "DD-1002"
        assert row["total_qty"] == 40
        assert row["total_orders"] == 40
        assert row["shopify_sales"] == 20
        assert row["shopify_sales_pct"] == 6.15  # 20 of the sheet's own 325
        assert row["total_complaints"] == 20
        assert row["status"] == "attention"

    def test_search_is_case_insensitive(self, store: TestClient) -> None:
        assert store.get(f"{PERFORMANCE}?search=dd-1003").json()["total"] == 1

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            # Every list below is in the shared default order: total complaints
            # descending. DD-1002 has 20, DD-1001 one.
            ("min_sales=20", ["DD-1002", "DD-1001"]),
            ("max_sales=5", ["DD-1004", "DD-1003"]),
            ("min_qty=100", ["DD-1001", "DD-1003"]),
            ("max_qty=40", ["DD-1002", "DD-1004"]),
            ("status=critical", []),
            ("status=attention", ["DD-1002", "DD-1004", "DD-1003"]),
            ("min_sales_pct=50", ["DD-1001"]),
            (f"complaint_category={DEFECT}", ["DD-1002", "DD-1001"]),
            # Two filters that cannot both hold.
            ("status=excellent&min_qty=500", []),
        ],
    )
    def test_filters(self, store: TestClient, query: str, expected: list[str]) -> None:
        body = store.get(f"{PERFORMANCE}?{query}").json()

        assert [row["sku"] for row in body["rows"]] == expected
        assert body["total"] == len(expected)

    @pytest.mark.parametrize(
        ("sort", "descending", "first"),
        [
            ("shopify_sales", "true", "DD-1001"),
            ("shopify_sales", "false", "DD-1003"),
            ("total_qty", "true", "DD-1003"),
            ("status", "false", "DD-1002"),
            ("sku", "false", "DD-1001"),
        ],
    )
    def test_sorting(self, store: TestClient, sort: str, descending: str, first: str) -> None:
        body = store.get(f"{PERFORMANCE}?sort={sort}&descending={descending}").json()

        assert body["rows"][0]["sku"] == first
        assert body["sort"] == sort

    def test_the_tiebreak_stays_alphabetical_in_both_directions(
        self, signed_in: TestClient
    ) -> None:
        """Reversing the sort must not reverse the tiebreak with it."""
        import_sheet(signed_in, [sheet_row("ZZZ", 5), sheet_row("AAA", 5)])

        ascending = signed_in.get(f"{PERFORMANCE}?sort=total_qty&descending=false").json()
        descending = signed_in.get(f"{PERFORMANCE}?sort=total_qty&descending=true").json()

        assert [r["sku"] for r in ascending["rows"]] == ["AAA", "ZZZ"]
        assert [r["sku"] for r in descending["rows"]] == ["AAA", "ZZZ"]

    def test_pagination_reports_the_filtered_total(self, store: TestClient) -> None:
        body = store.get(f"{PERFORMANCE}?limit=2&offset=2").json()

        assert len(body["rows"]) == 2
        assert body["total"] == 4
        assert body["offset"] == 2

    def test_an_unknown_sort_key_is_refused(self, store: TestClient) -> None:
        """A whitelist, because the value arrives from a query string."""
        assert store.get(f"{PERFORMANCE}?sort=quantity_on_hand").status_code == 422
        assert store.get(f"{PERFORMANCE}?sort=1;DROP+TABLE").status_code == 422

    def test_an_unknown_status_is_refused(self, store: TestClient) -> None:
        assert store.get(f"{PERFORMANCE}?status=terrible").status_code == 422

    def test_an_unknown_complaint_category_says_which_are_known(self, store: TestClient) -> None:
        """Silently matching nothing would read as "no results" instead."""
        response = store.get(f"{PERFORMANCE}?complaint_category=item_exploded")

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "unknown_complaint_category"
        assert DEFECT in error["detail"]["known"]
        assert error["next"]

    def test_an_empty_workspace_is_an_empty_page(self, signed_in: TestClient) -> None:
        body = signed_in.get(PERFORMANCE).json()

        assert body["rows"] == []
        assert body["total"] == 0

    def test_the_table_agrees_with_the_cards(self, store: TestClient) -> None:
        """The one-read design's promise, asserted across two HTTP calls."""
        kpis = store.get(INSIGHTS).json()["kpis"]
        rows = store.get(f"{PERFORMANCE}?limit=200").json()["rows"]

        assert len(rows) == kpis["total_skus"]
        assert sum(row["shopify_sales"] for row in rows) == kpis["shopify_sales"]
        assert sum(row["total_complaints"] for row in rows) == kpis["total_complaints"]
        assert sum(row["total_qty"] for row in rows) == kpis["total_qty"]
        assert sum(row["total_orders"] for row in rows) == kpis["total_orders"]


class TestTheDateRange:
    """The window the whole response is computed over.

    Two ways to say it: a ``days`` preset, or an explicit ``since``/``until``
    pair for the custom control. They resolve to the same kind of window, and
    every figure in the response — sales, complaints, the percentage
    denominators — comes from it. A response computed half over one window and
    half over another would be internally inconsistent in a way no reader could
    detect from the page.
    """

    def test_the_presets_the_page_offers_all_answer(self, store: TestClient) -> None:
        for days in (30, 60, 90, 180):
            body = store.get(f"{PERFORMANCE}?days={days}").json()
            assert body["days"] == days

    def test_an_explicit_pair_wins_over_the_preset(self, store: TestClient) -> None:
        today = date.today()
        since, until = today - timedelta(days=6), today
        body = store.get(f"{PERFORMANCE}?days=180&since={since}&until={until}").json()

        # Seven days inclusive, not the 180 that was also sent.
        assert body["days"] == 7

    def test_a_window_before_the_sales_reports_no_sales(self, store: TestClient) -> None:
        """The orders sit 1–3 days back; a window that ends before them is empty."""
        until = date.today() - timedelta(days=30)
        since = until - timedelta(days=10)
        body = store.get(f"{PERFORMANCE}?since={since}&until={until}").json()

        assert body["total"] == 4  # the sheet's SKUs are still there…
        assert all(row["shopify_sales"] == 0 for row in body["rows"])  # …with no sales
        assert all(row["shopify_sales_pct"] == 0 for row in body["rows"])

    def test_the_window_bounds_the_sales_and_the_dated_complaints(self, store: TestClient) -> None:
        """This fixture imports complaints *with* dates, so both move.

        A window before every order and every complaint reports zero of each —
        which is the answer, not an absence of data. An aggregated sheet behaves
        differently on purpose; see `TestBothUploadFormats`.
        """
        today = date.today()
        empty_since = today - timedelta(days=40)
        empty_until = today - timedelta(days=30)

        covering = store.get(f"{PERFORMANCE}?days=30&limit=200").json()["rows"]
        outside = store.get(
            f"{PERFORMANCE}?since={empty_since}&until={empty_until}&limit=200"
        ).json()["rows"]

        by_sku = {r["sku"]: r for r in covering}
        assert by_sku["DD-1002"]["total_complaints"] == 20
        assert by_sku["DD-1002"]["shopify_sales"] == 20

        # Both move: the complaints carried dates, so the window selects them.
        outside_by_sku = {r["sku"]: r for r in outside}
        assert outside_by_sku["DD-1002"]["total_complaints"] == 0
        assert outside_by_sku["DD-1002"]["shopify_sales"] == 0
        assert outside_by_sku["DD-1002"]["shopify_sales_pct"] == 0.0

    def test_the_share_is_the_formula_on_every_row(self, store: TestClient) -> None:
        """On this table: (a SKU's sales ÷ the imported SKUs' sales) × 100."""
        rows = store.get(f"{PERFORMANCE}?days=30&limit=200").json()["rows"]
        imported = sum(r["shopify_sales"] for r in rows)

        for row in rows:
            assert row["shopify_sales_pct"] == round(row["shopify_sales"] / imported * 100, 2), row[
                "sku"
            ]
        assert sum(r["shopify_sales_pct"] for r in rows) == pytest.approx(100.0, abs=0.05)
        assert all(r["shopify_sales_pct"] <= 100 for r in rows)

    def test_the_cards_keep_the_store_denominator(self, store: TestClient) -> None:
        """The table is a composition of the sheet; the card is a share of the
        store. Different questions, and they are not meant to agree."""
        kpis = store.get(f"{INSIGHTS}?days=30").json()["kpis"]

        assert kpis["shopify_sales_all"] == 425  # includes DD-9999
        assert kpis["shopify_sales_pct"] == 76.47  # 325 of 425

    def test_a_window_covering_the_sales_reports_them(self, store: TestClient) -> None:
        today = date.today()
        body = store.get(f"{PERFORMANCE}?since={today - timedelta(days=7)}&until={today}").json()

        by_sku = {row["sku"]: row for row in body["rows"]}
        assert by_sku["DD-1001"]["shopify_sales"] == 300

    def test_a_backwards_range_is_refused(self, store: TestClient) -> None:
        today = date.today()
        r = store.get(f"{PERFORMANCE}?since={today}&until={today - timedelta(days=1)}")

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_date_range"

    def test_half_a_range_is_refused(self, store: TestClient) -> None:
        """Guessing the missing bound would answer a question nobody asked."""
        r = store.get(f"{PERFORMANCE}?since={date.today()}")

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_date_range"

    def test_a_range_longer_than_the_rollup_is_refused(self, store: TestClient) -> None:
        """``days`` is capped at 365; the explicit form cannot be a way around it."""
        until = date.today()
        r = store.get(f"{PERFORMANCE}?since={until - timedelta(days=500)}&until={until}")

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_date_range"

    def test_the_export_carries_the_window_it_was_asked_for(self, store: TestClient) -> None:
        """The download and the screen must not disagree about the period."""
        today = date.today()
        window = f"since={today - timedelta(days=30)}&until={today - timedelta(days=20)}"
        rows = store.get(f"{PERFORMANCE}?{window}").json()["rows"]
        body = store.get(f"{PERFORMANCE}/export?{window}").text

        assert sum(row["shopify_sales"] for row in rows) == 0
        # Every sales figure in the file is zero for a window before the orders.
        for line in body.splitlines()[1:]:
            cells = line.split(",")
            assert cells[2] == "0"  # Shopify Sales
            assert cells[3] == "0.00"  # Shopify Sales %


class TestTheDashboardIsUnaffected:
    """The two pages must be able to change independently."""

    def test_the_dashboard_endpoints_still_answer(self, store: TestClient) -> None:
        assert store.get("/api/analytics/overview").status_code == 200
        assert store.get("/api/analytics/kpis").status_code == 200
        assert store.get("/api/analytics/skus").status_code == 200

    def test_both_pages_report_the_same_totals(self, store: TestClient) -> None:
        """Different queries, different modules — the same answer."""
        dashboard = store.get("/api/analytics/kpis").json()
        analytics = store.get(INSIGHTS).json()["kpis"]

        assert dashboard["total_skus"] == analytics["total_skus"]
        assert dashboard["shopify_sales"] == analytics["shopify_sales"]
        assert dashboard["total_complaints"] == analytics["total_complaints"]
        assert dashboard["total_orders"] == analytics["total_orders"]
        assert dashboard["shopify_sales_all"] == analytics["shopify_sales_all"]
        assert dashboard["shopify_sales_pct"] == analytics["shopify_sales_pct"]


class TestPerformanceExport:
    """The table as a file. Same rows, same filters, rendered by the report writers."""

    EXPORT = f"{PERFORMANCE}/export"

    def test_csv_carries_the_columns_the_table_shows(self, store: TestClient) -> None:
        response = store.get(f"{self.EXPORT}?format=csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert "sku-performance-" in response.headers["content-disposition"]

        # to_csv writes the header row first — no title rows above it.
        header = response.content.decode("utf-8-sig").splitlines()[0]
        assert header.split(",") == [
            "SKU",
            "Complaints",
            "Shopify Sales",
            "Shopify Sales %",
            "Total Quantity",
            "Total Orders",
            "Missing",
            "Missing Part",
            "Wrong Item Delivered",
            "Order Wrong Parcel",
            "Item Defect Partial",
            "Item Defect Complete",
            "Item Damage Partial",
            "Item Damage Complete",
            "Electronics Nonworking Partial",
            "Electronics Nonworking Complete",
        ]

    def test_the_export_columns_are_the_pages_columns(self, store: TestClient) -> None:
        """One list, so a download and the screen cannot describe different tables."""
        from app.services.insights import EXPORT_CATEGORY_COLUMNS, EXPORT_SUMMARY_COLUMNS

        header = store.get(f"{self.EXPORT}?format=csv").content.decode("utf-8-sig").splitlines()[0]
        expected = [h for h, _align in EXPORT_SUMMARY_COLUMNS] + [
            h for _attribute, h in EXPORT_CATEGORY_COLUMNS
        ]
        assert header.split(",") == expected

    def test_csv_holds_every_row_not_just_a_page(self, store: TestClient) -> None:
        """An export that quietly ended at 50 rows would be a trap."""
        text = store.get(f"{self.EXPORT}?format=csv").content.decode("utf-8-sig")

        for sku in ("DD-1001", "DD-1002", "DD-1003", "DD-1004"):
            assert sku in text

    def test_the_figures_match_the_table(self, store: TestClient) -> None:
        rows = {r["sku"]: r for r in store.get(f"{PERFORMANCE}?limit=200").json()["rows"]}
        text = store.get(f"{self.EXPORT}?format=csv").content.decode("utf-8-sig")

        line = next(line for line in text.splitlines() if line.startswith("DD-1002"))
        cells = line.split(",")
        row = rows["DD-1002"]
        assert cells[1] == str(row["total_complaints"])
        assert cells[2] == str(row["shopify_sales"])
        assert cells[3] == f"{row['shopify_sales_pct']:.2f}"
        assert cells[4] == str(row["total_qty"])
        assert cells[5] == str(row["total_orders"])

    def test_percentages_are_plain_numbers_a_spreadsheet_can_average(
        self, store: TestClient
    ) -> None:
        text = store.get(f"{self.EXPORT}?format=csv").content.decode("utf-8-sig")

        line = next(line for line in text.splitlines() if line.startswith("DD-1001"))
        assert "%" not in line
        assert float(line.split(",")[5]) > 0

    def test_the_filters_apply_to_the_export(self, store: TestClient) -> None:
        text = store.get(f"{self.EXPORT}?format=csv&status=excellent").content.decode("utf-8-sig")

        # Only DD-1001 sells above the median with a clean record.
        assert "DD-1001" in text
        for sku in ("DD-1002", "DD-1003", "DD-1004"):
            assert sku not in text

    def test_the_sort_applies_to_the_export(self, store: TestClient) -> None:
        text = store.get(f"{self.EXPORT}?format=csv&sort=sku&descending=false").content.decode(
            "utf-8-sig"
        )
        skus = [line.split(",")[0] for line in text.splitlines() if line.startswith("DD-")]

        assert skus == sorted(skus)

    def test_xlsx_is_a_real_workbook(self, store: TestClient) -> None:
        response = store.get(f"{self.EXPORT}?format=xlsx")

        assert response.status_code == 200
        assert "spreadsheetml" in response.headers["content-type"]
        # A zip archive, which is what .xlsx is.
        assert response.content[:2] == b"PK"

    def test_a_sku_that_looks_like_a_formula_is_neutralised(self, signed_in: TestClient) -> None:
        """The reason the export goes through the report writers at all."""
        import_sheet(signed_in, [sheet_row("=cmd|'/c calc'!A1", 5)])

        text = signed_in.get(f"{self.EXPORT}?format=csv").content.decode("utf-8-sig")

        assert "\n=cmd" not in text
        assert "'=cmd" in text  # apostrophe-prefixed, so Excel treats it as text

    def test_an_unknown_format_is_refused(self, store: TestClient) -> None:
        assert store.get(f"{self.EXPORT}?format=pdf").status_code == 422
        assert store.get(f"{self.EXPORT}?format=exe").status_code == 422

    def test_an_unknown_complaint_category_is_refused_here_too(self, store: TestClient) -> None:
        response = store.get(f"{self.EXPORT}?format=csv&complaint_category=item_exploded")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unknown_complaint_category"

    def test_it_is_never_cached(self, store: TestClient) -> None:
        """A filtered export is a snapshot; a cache would serve stale figures."""
        response = store.get(f"{self.EXPORT}?format=csv")

        assert response.headers["cache-control"] == "no-store"

    def test_an_empty_workspace_exports_an_empty_table(self, signed_in: TestClient) -> None:
        response = signed_in.get(f"{self.EXPORT}?format=csv")

        assert response.status_code == 200
        assert "SKU" in response.content.decode("utf-8-sig")

    def test_it_requires_a_session(self, api: TestClient) -> None:
        assert api.get(f"{self.EXPORT}?format=csv").status_code == 401


class TestAnalyticsAfterAFlexibleImport:
    """The whole point of the flexible import: the figures come out right.

    A raw complaint export — one row per complaint, columns named whatever the
    complaints system calls them — goes in, and Total SKUs, Total Quantity,
    Shopify Sales, Shopify Sales % and Total Complaints come out.
    """

    #: Two SKUs. DD-1 has 3 complaint rows across 2 orders, DD-2 has 2 across 2.
    #:
    #: Dates are relative to today rather than fixed, because the complaint
    #: counts these tests assert are now *windowed*: a hard-coded 2026-07-01
    #: falls out of the default 30-day range the moment the clock moves past it,
    #: and the suite would start failing on a date rather than on a change.
    def raw(self, *, days_ago: int = 2) -> str:
        day = (date.today() - timedelta(days=days_ago)).isoformat()
        later = (date.today() - timedelta(days=days_ago - 1)).isoformat()
        return (
            "Date,Invoice No,Product Code,Issue Type,Handled By\n"
            f"{day},INV-1,DD-1,Damage,anita\n"
            f"{day},INV-1,DD-1,Missing Part,anita\n"
            f"{later},INV-2,DD-1,Non Working,ravi\n"
            f"{later},INV-3,DD-2,Wrong Item,ravi\n"
            f"{later},INV-4,DD-2,Missing,sam\n"
        )

    def upload(self, client: TestClient, *, days_ago: int = 2) -> dict:
        import io as _io

        response = client.post(
            "/api/imports/upload",
            files={
                "file": (
                    "complaints.csv",
                    _io.BytesIO(self.raw(days_ago=days_ago).encode()),
                    "text/csv",
                )
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_import_reports_what_it_did(self, signed_in: TestClient) -> None:
        body = self.upload(signed_in)

        assert body["sheet_format"] == "complaints"
        assert body["batch"]["rows_read"] == 5
        assert body["items_created"] == 2
        assert body["unmapped_reasons"] == {}

    def test_the_six_figures(self, signed_in: TestClient) -> None:
        self.upload(signed_in)
        # Shopify sells one of the two imported SKUs, plus one it has never heard
        # of — which belongs in no percentage at all.
        sell(signed_in, [("DD-1", 30, 19900, 1), ("WEB-9", 70, 19900, 1)])
        rebuild(signed_in)

        k = signed_in.get(f"{INSIGHTS}?days=30").json()["kpis"]

        assert k["total_skus"] == 2
        # 3 complaint rows for DD-1, 2 for DD-2, one unit each.
        assert k["total_qty"] == 5
        assert k["total_orders"] == 4  # INV-1 (twice, one order), INV-2, INV-3, INV-4
        assert k["total_complaints"] == 5
        assert k["shopify_sales"] == 30  # DD-1 only; WEB-9 is not in the sheet
        assert k["shopify_sales_all"] == 100  # …but it is in the denominator
        assert k["shopify_sales_pct"] == 30.0

    def test_shopify_matches_on_sku_alone(self, signed_in: TestClient) -> None:
        """No product, variant, vendor or inventory feed is consulted."""
        self.upload(signed_in)
        sell(signed_in, [("dd 1", 12, 19900, 1)])  # same SKU, different spelling
        rebuild(signed_in)

        rows = {r["sku"]: r for r in signed_in.get(f"{PERFORMANCE}?limit=10").json()["rows"]}

        assert rows["DD-1"]["shopify_sales"] == 12
        assert rows["DD-2"]["shopify_sales"] == 0

    def test_a_sku_with_no_shopify_sales_is_kept_not_dropped(self, signed_in: TestClient) -> None:
        self.upload(signed_in)

        body = signed_in.get(f"{PERFORMANCE}?limit=10").json()

        assert body["total"] == 2
        assert all(row["shopify_sales"] == 0 for row in body["rows"])

    def test_the_complaint_breakdown_reaches_the_analytics(self, signed_in: TestClient) -> None:
        self.upload(signed_in)

        categories = {
            c["field_name"]: c["count"]
            for c in signed_in.get(INSIGHTS).json()["complaints"]["categories"]
        }

        assert categories["item_damage_complete"] == 1
        assert categories["missing_part"] == 1
        assert categories["electronics_nonworking_complete"] == 1
        assert categories["item_mismatch_wrong_item"] == 1
        assert categories["missing"] == 1

    def test_the_window_selects_the_complaints(self, signed_in: TestClient) -> None:
        """End to end: this export carries dates, so the range applies to it."""
        self.upload(signed_in, days_ago=45)
        sell(signed_in, [("DD-1", 30, 19900, 1)])
        rebuild(signed_in)

        narrow = signed_in.get(f"{INSIGHTS}?days=30").json()
        wide = signed_in.get(f"{INSIGHTS}?days=90").json()

        assert narrow["kpis"]["total_complaints"] == 0  # all five are 45 days back
        assert wide["kpis"]["total_complaints"] == 5
        # Nothing is unaccounted for, so the page has no caveat to render.
        assert narrow["complaint_scope"]["undated_complaints"] == 0
        assert narrow["complaint_scope"]["filtered_by_date"] is True

    def test_a_custom_range_bounds_the_sales_and_the_complaints(
        self, signed_in: TestClient
    ) -> None:
        self.upload(signed_in, days_ago=10)
        sell(signed_in, [("DD-1", 40, 19900, 10)])
        rebuild(signed_in)

        today = date.today()
        covering = signed_in.get(
            f"{PERFORMANCE}?since={today - timedelta(days=12)}&until={today}&limit=10"
        ).json()
        before = signed_in.get(
            f"{PERFORMANCE}?since={today - timedelta(days=60)}&until={today - timedelta(days=30)}"
            "&limit=10"
        ).json()

        assert sum(r["total_complaints"] for r in covering["rows"]) == 5
        assert sum(r["total_complaints"] for r in before["rows"]) == 0
        assert sum(r["shopify_sales"] for r in covering["rows"]) == 40
        assert sum(r["shopify_sales"] for r in before["rows"]) == 0

    def test_re_importing_the_same_export_does_not_double_the_totals(
        self, signed_in: TestClient
    ) -> None:
        """Counts are replaced, not incremented: the sheet is a restatement."""
        self.upload(signed_in)
        self.upload(signed_in)

        k = signed_in.get(INSIGHTS).json()["kpis"]

        assert k["total_skus"] == 2
        assert k["total_complaints"] == 5


class TestTheReportsCentreMatchesThePage:
    """The Reports centre builds SKU performance through the page's own code.

    It used to build its own table and had drifted three ways at once: ordered
    by Shopify sales where the page orders by total complaints, carrying both
    ``Quantity`` and ``Total Qty`` where the page shows one quantity column, and
    with no per-category breakdown at all. Anyone reconciling the download
    against the screen found three differences and no way to tell which was
    right.
    """

    def _report(self, days: int = 30, limit: int = 50_000) -> object:
        from app.db import session as session_module
        from app.services import report_data

        with session_module.get_session_factory()() as db:
            return report_data.sku_performance_report(db, workspace_id=1, days=days, limit=limit)

    def test_the_columns_are_the_pages_columns(self, store: TestClient) -> None:
        from app.services.insights import EXPORT_CATEGORY_COLUMNS, EXPORT_SUMMARY_COLUMNS

        report = self._report()
        headers = [c.header for c in report.columns]  # type: ignore[attr-defined]

        assert headers == [h for h, _a in EXPORT_SUMMARY_COLUMNS] + [
            h for _f, h in EXPORT_CATEGORY_COLUMNS
        ]

    def test_the_ordering_is_the_pages_ordering(self, store: TestClient) -> None:
        report = self._report()
        page = store.get(f"{PERFORMANCE}?limit=200").json()["rows"]

        assert [r[0] for r in report.rows] == [r["sku"] for r in page]  # type: ignore[attr-defined]

    def test_every_figure_matches_the_page(self, store: TestClient) -> None:
        report = self._report()
        page = {r["sku"]: r for r in store.get(f"{PERFORMANCE}?limit=200").json()["rows"]}
        headers = [c.header for c in report.columns]  # type: ignore[attr-defined]
        at = {h: i for i, h in enumerate(headers)}

        for row in report.rows:  # type: ignore[attr-defined]
            ui = page[row[0]]
            assert row[at["Complaints"]] == str(ui["total_complaints"])
            assert row[at["Shopify Sales"]] == str(ui["shopify_sales"])
            assert row[at["Shopify Sales %"]] == f"{ui['shopify_sales_pct']:.2f}"
            assert row[at["Total Quantity"]] == str(ui["total_qty"])
            assert row[at["Total Orders"]] == str(ui["total_orders"])
            for field, header in [
                ("missing", "Missing"),
                ("item_defect_partial", "Item Defect Partial"),
                ("order_wrong_parcel", "Order Wrong Parcel"),
            ]:
                assert row[at[header]] == str(ui["complaints"].get(field, 0))

    def test_the_download_and_the_report_are_the_same_table(self, store: TestClient) -> None:
        """Two routes to one file: the page's export button and the Export Centre."""
        report = self._report()
        exported = store.get(f"{PERFORMANCE}/export?format=csv").content.decode("utf-8-sig")
        lines = exported.splitlines()

        assert lines[0].split(",") == [c.header for c in report.columns]  # type: ignore[attr-defined]
        assert lines[1].split(",")[0] == report.rows[0][0]  # type: ignore[attr-defined]

    def test_top_n_cuts_the_same_ordering(self, store: TestClient) -> None:
        """ "Export Top 50 only" must mean the page's top 50, not another 50."""
        capped = self._report(limit=2)
        full = self._report()

        assert len(capped.rows) == 2  # type: ignore[attr-defined]
        assert [r[0] for r in capped.rows] == [r[0] for r in full.rows][:2]  # type: ignore[attr-defined]
        assert capped.truncated is True  # type: ignore[attr-defined]

    def test_the_date_range_reaches_the_report(self, store: TestClient) -> None:
        """Sales follow `days` in the file, and so do dated complaints."""
        headers = [c.header for c in self._report().columns]  # type: ignore[attr-defined]
        at = {h: i for i, h in enumerate(headers)}

        narrow = {r[0]: r for r in self._report(days=1).rows}  # type: ignore[attr-defined]
        wide = {r[0]: r for r in self._report(days=365).rows}  # type: ignore[attr-defined]

        assert narrow.keys() == wide.keys()
        # A one-day window sees none of the orders…
        assert all(narrow[sku][at["Shopify Sales"]] == "0" for sku in narrow)
        assert all(narrow[sku][at["Shopify Sales %"]] == "0.00" for sku in narrow)
        assert any(wide[sku][at["Shopify Sales"]] != "0" for sku in wide)
        # …nor any of the complaints, which this fixture imported with dates on
        # them. The file says exactly what the screen says.
        assert all(narrow[sku][at["Complaints"]] == "0" for sku in narrow)
        assert any(wide[sku][at["Complaints"]] != "0" for sku in wide)

    def test_the_percentage_in_the_file_is_the_formula(self, store: TestClient) -> None:
        report = self._report(days=365)
        headers = [c.header for c in report.columns]  # type: ignore[attr-defined]
        at = {h: i for i, h in enumerate(headers)}
        imported = sum(int(r[at["Shopify Sales"]]) for r in report.rows)  # type: ignore[attr-defined]

        for row in report.rows:  # type: ignore[attr-defined]
            sales = int(row[at["Shopify Sales"]])
            share = f"{(sales / imported * 100) if imported else 0.0:.2f}"
            assert row[at["Shopify Sales %"]] == share, row[0]
