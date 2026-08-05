"""Reports: preview, generation, the Export Centre, download and delete.

Built on real imported rows so a report is exercised against the same analytics
the dashboard reads — the whole point of §12 is that an export and the screen
it came from agree.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session as session_module
from app.models import Report
from app.services import reports as reports_service

# The order helpers live with the analytics tests; a report is built from the
# same rollup, so reusing them keeps one definition of "this SKU sold".
from tests.test_analytics_api import rebuild, sell

REPORTS = "/api/reports"
PREVIEW = "/api/reports/preview"


def import_stock(client: TestClient, rows: list[tuple[str, str, int]]) -> None:
    body = "SKU,Product Name,Quantity\n" + "".join(
        f"{sku},{name},{qty}\n" for sku, name, qty in rows
    )
    response = client.post(
        "/api/imports/upload",
        files={"file": ("stock.csv", io.BytesIO(body.encode()), "text/csv")},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def stocked(signed_in: TestClient) -> TestClient:
    import_stock(
        signed_in,
        [
            ("DD-1001", "Steel Bottle 750ml", 100),
            ("DD-1002", "Blue Hoodie", 4),
            ("DD-1003", "Wall Hook", 0),
        ],
    )
    return signed_in


def generate(
    client: TestClient, kind: str = "inventory", fmt: str = "csv", **extra: object
) -> dict:
    response = client.post(REPORTS, json={"kind": kind, "fmt": fmt, **extra})
    assert response.status_code == 202, response.text
    return response.json()


class TestAuthentication:
    def test_every_endpoint_requires_a_session(self, api: TestClient) -> None:
        assert api.get(REPORTS).status_code == 401
        assert api.get(PREVIEW, params={"kind": "inventory"}).status_code == 401
        assert api.post(REPORTS, json={"kind": "inventory", "fmt": "csv"}).status_code == 401
        assert api.get(f"{REPORTS}/1/download").status_code == 401
        assert api.delete(f"{REPORTS}/1").status_code == 401


class TestPreview:
    @pytest.mark.parametrize(
        "kind", ["inventory", "sales", "sku_performance", "dashboard", "sku_matching"]
    )
    def test_each_report_type_previews(self, stocked: TestClient, kind: str) -> None:
        body = stocked.get(PREVIEW, params={"kind": kind}).json()

        assert body["title"]
        assert body["columns"]
        assert all(len(row) == len(body["columns"]) for row in body["rows"])

    def test_the_preview_shows_real_rows(self, stocked: TestClient) -> None:
        body = stocked.get(PREVIEW, params={"kind": "inventory"}).json()

        assert any("DD-1001" in row for row in body["rows"])

    def test_columns_carry_their_alignment(self, stocked: TestClient) -> None:
        """So the client right-aligns figures without re-deriving which are numeric."""
        body = stocked.get(PREVIEW, params={"kind": "inventory"}).json()

        assert {column["align"] for column in body["columns"]} == {"left", "right"}

    def test_the_preview_is_capped_and_says_so(self, stocked: TestClient) -> None:
        body = stocked.get(PREVIEW, params={"kind": "inventory", "limit": 1}).json()

        assert len(body["rows"]) == 1
        assert body["truncated"] is True

    def test_an_unknown_report_type_is_refused(self, stocked: TestClient) -> None:
        assert stocked.get(PREVIEW, params={"kind": "profit"}).status_code == 422

    def test_an_unknown_range_is_refused(self, stocked: TestClient) -> None:
        response = stocked.get(PREVIEW, params={"kind": "sales", "range_option": "everything"})

        assert response.status_code == 422

    def test_the_financial_year_range_is_accepted(self, stocked: TestClient) -> None:
        response = stocked.get(PREVIEW, params={"kind": "sales", "range_option": "fy"})

        assert response.status_code == 200

    def test_an_empty_workspace_previews_without_rows(self, signed_in: TestClient) -> None:
        body = signed_in.get(PREVIEW, params={"kind": "inventory"}).json()

        assert body["rows"] == []
        assert body["columns"]


class TestGeneration:
    @pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf"])
    def test_each_format_is_generated(self, stocked: TestClient, fmt: str) -> None:
        report = generate(stocked, fmt=fmt)

        assert report["status"] == "ready"
        assert report["size_bytes"] > 0
        assert report["filename"].endswith(f".{fmt}")

    @pytest.mark.parametrize(
        "kind", ["inventory", "sales", "sku_performance", "dashboard", "sku_matching"]
    )
    def test_each_report_type_is_generated(self, stocked: TestClient, kind: str) -> None:
        assert generate(stocked, kind=kind)["status"] == "ready"

    def test_the_request_is_accepted_before_the_file_exists(self, stocked: TestClient) -> None:
        """202, not 200: §12.2's flow is Preparing → Ready."""
        response = stocked.post(REPORTS, json={"kind": "inventory", "fmt": "csv"})

        assert response.status_code == 202

    def test_the_row_count_is_recorded(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="inventory")

        assert report["row_count"] == 3

    def test_the_range_is_recorded_in_words(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="sales", range_option="90")

        assert report["range_days"] == 90
        assert report["range_label"] == "Last 90 days"

    def test_the_financial_year_label_names_its_start(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="sales", range_option="fy")

        assert report["range_label"].startswith("This financial year (from 1 Apr")

    def test_an_unknown_format_is_refused(self, stocked: TestClient) -> None:
        assert stocked.post(REPORTS, json={"kind": "inventory", "fmt": "docx"}).status_code == 422

    def test_a_report_can_be_generated_with_no_data(self, signed_in: TestClient) -> None:
        """An empty sheet is still a valid, downloadable export."""
        report = generate(signed_in)

        assert report["status"] == "ready"
        assert report["row_count"] == 0

    def test_a_failure_is_recorded_rather_than_raised(
        self, stocked: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(reports_service.report_files, "render", boom)
        report = stocked.post(REPORTS, json={"kind": "inventory", "fmt": "csv"}).json()

        assert report["status"] == "failed"
        assert report["error_code"] == "report_failed"
        # The exception text belongs in the log, not on the user's screen.
        assert "disk on fire" not in (report["error_detail"] or "")


class TestDashboardSnapshot:
    """The Dashboard header's export — the same engine, a different builder."""

    def test_it_reports_metrics_rather_than_a_row_per_sku(self, stocked: TestClient) -> None:
        """
        The three other reports are already per-SKU tables. A snapshot that
        repeated one of them would be a duplicate with a different name; what
        the dashboard actually shows is its cards.
        """
        body = stocked.get(PREVIEW, params={"kind": "dashboard"}).json()

        assert body["title"] == "Dashboard snapshot"
        assert [column["header"] for column in body["columns"]] == ["Metric", "Value"]
        assert [row[0] for row in body["rows"]][:4] == [
            "Total SKUs",
            "Total quantity",
            "Shopify sales (matched)",
            "Shopify sales %",
        ]

    def test_it_carries_every_card_the_dashboard_shows(self, stocked: TestClient) -> None:
        body = stocked.get(PREVIEW, params={"kind": "dashboard"}).json()
        metrics = {row[0] for row in body["rows"]}

        for card in (
            "Total SKUs",
            "Total quantity",
            "Shopify sales (matched)",
            "Shopify sales %",
            "Total orders",
            "Total complaints",
        ):
            assert card in metrics, card

    def test_the_figures_match_the_dashboard_api(self, stocked: TestClient) -> None:
        """
        The point of an export: it cannot disagree with the screen it was taken
        from. Both read services.analytics.kpis, and this is what proves it.
        """
        kpis = stocked.get("/api/analytics/kpis", params={"days": 30}).json()
        rows = dict(stocked.get(PREVIEW, params={"kind": "dashboard"}).json()["rows"])

        assert rows["Total SKUs"] == str(kpis["total_skus"])
        assert rows["Total quantity"] == str(kpis["total_quantity"])
        assert rows["Shopify sales (matched)"] == str(kpis["shopify_sales"])
        assert rows["Total orders"] == str(kpis["total_orders"])
        assert rows["Total complaints"] == str(kpis["total_complaints"])

    def test_it_states_the_denominator_behind_the_percentage(self, stocked: TestClient) -> None:
        """A share with an invisible denominator is a number nobody can check."""
        rows = dict(stocked.get(PREVIEW, params={"kind": "dashboard"}).json()["rows"])

        assert "Shopify sales (all SKUs)" in rows

    def test_the_range_reaches_the_figures(self, stocked: TestClient) -> None:
        narrow = stocked.get(PREVIEW, params={"kind": "dashboard", "range_option": "7"}).json()

        assert "last 7 days" in narrow["subtitle"]

    @pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf"])
    def test_it_writes_in_every_existing_format(self, stocked: TestClient, fmt: str) -> None:
        """No new writer: the three the Export Centre already has."""
        report = generate(stocked, kind="dashboard", fmt=fmt)

        assert report["status"] == "ready"
        assert report["size_bytes"] > 0
        assert report["filename"].endswith(f".{fmt}")

    def test_it_is_named_for_what_it_is(self, stocked: TestClient) -> None:
        assert "dashboard" in generate(stocked, kind="dashboard")["filename"]

    def test_it_appears_in_the_export_centre_like_any_other(self, stocked: TestClient) -> None:
        """Exported from the Dashboard, listed and downloadable in one place."""
        report = generate(stocked, kind="dashboard")

        listed = stocked.get(REPORTS).json()["items"]
        assert any(item["id"] == report["id"] for item in listed)
        assert stocked.get(f"{REPORTS}/{report['id']}/download").status_code == 200


class TestExportLabels:
    """A label that names the wrong thing is worse than no label."""

    def test_the_dashboard_snapshot_calls_shopify_revenue_what_it_is(
        self, stocked: TestClient
    ) -> None:
        """
        This row read "Stock value" and carried ``Kpis.revenue_paise``, which is
        *matched Shopify revenue* over the window -- not the value of stock on
        hand. On the real store it presented 3 crore of sales as inventory.
        """
        rows = dict(stocked.get(PREVIEW, params={"kind": "dashboard"}).json()["rows"])

        assert "Shopify revenue (matched)" in rows
        assert "Stock value" not in rows

    def test_reports_carrying_shopify_sales_state_the_refund_limitation(
        self, stocked: TestClient
    ) -> None:
        for kind in ("sales", "dashboard"):
            subtitle = stocked.get(PREVIEW, params={"kind": kind}).json()["subtitle"]
            assert "Partially refunded" in subtitle, kind


class TestSkuMatchingReport:
    """Which imported SKUs Shopify sold, and which it did not."""

    def test_it_names_every_column_the_report_promises(self, stocked: TestClient) -> None:
        body = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()

        assert [c["header"] for c in body["columns"]] == [
            "SKU",
            "Imported Quantity",
            "Complaint Quantity",
            "Shopify Sales",
            "Match Status",
        ]

    def test_zero_sales_and_matched_is_a_contradiction(self, stocked: TestClient) -> None:
        rows = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()["rows"]

        assert rows
        for row in rows:
            assert (row[3] == "0") == (row[4] == "Unmatched"), row

    def test_unmatched_skus_come_first(self, stocked: TestClient) -> None:
        """The finding leads; a matched SKU is not what anyone opened this for."""
        rows = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()["rows"]
        statuses = [row[4] for row in rows]

        assert statuses == sorted(statuses, key=lambda s: s == "Matched")

    def test_the_subtitle_counts_the_unmatched(self, stocked: TestClient) -> None:
        body = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()

        assert "imported SKUs had no Shopify sale" in body["subtitle"]

    @pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf"])
    def test_it_exports_in_every_existing_format(self, stocked: TestClient, fmt: str) -> None:
        report = generate(stocked, kind="sku_matching", fmt=fmt)

        assert report["status"] == "ready"
        assert report["size_bytes"] > 0


class TestTheReportsShareTheScreensDenominator:
    """Two report builders carry Shopify Sales %; both are the screen's figure.

    The Sales report and the SKU Performance report reach it by different
    routes — `analytics.sku_table` and `insights.performance` — so nothing but a
    test stops them drifting apart, or away from the page they come from.
    """

    @pytest.fixture
    def sold(self, stocked: TestClient) -> TestClient:
        # DD-9999 is not in the sheet: it separates the store's total from the
        # imported SKUs', which is the whole point of the two denominators.
        sell(
            stocked,
            [("DD-1001", 30, 19900, 1), ("DD-1002", 10, 19900, 2), ("DD-9999", 60, 19900, 3)],
        )
        rebuild(stocked)
        return stocked

    def shares(self, client: TestClient, kind: str) -> dict[str, str]:
        body = client.get(PREVIEW, params={"kind": kind}).json()
        at = {column["header"]: i for i, column in enumerate(body["columns"])}
        return {row[0]: row[at["Shopify Sales %"]] for row in body["rows"]}

    def test_both_reports_divide_by_the_imported_skus_own_sales(self, sold: TestClient) -> None:
        for kind in ("sales", "sku_performance"):
            shares = self.shares(sold, kind)

            assert shares["DD-1001"] == "75.00", kind  # 30 of the sheet's 40, not of 100
            assert shares["DD-1002"] == "25.00", kind

    def test_the_two_reports_agree_row_for_row(self, sold: TestClient) -> None:
        assert self.shares(sold, "sales") == self.shares(sold, "sku_performance")

    def test_the_reports_match_what_the_screen_shows(self, sold: TestClient) -> None:
        on_screen = {
            row["sku"]: f"{row['shopify_sales_pct']:.2f}"
            for row in sold.get("/api/analytics/skus").json()["rows"]
        }

        assert self.shares(sold, "sales") == on_screen

    def test_the_exported_column_sums_to_one_hundred(self, sold: TestClient) -> None:
        shares = self.shares(sold, "sku_performance")

        assert sum(float(value) for value in shares.values()) == 100.0

    def test_the_card_in_the_snapshot_keeps_the_store_denominator(self, sold: TestClient) -> None:
        """The reports moved; the KPI did not."""
        assert sold.get("/api/analytics/kpis").json()["shopify_sales_pct"] == 40.0


class TestTopOnlyExport:
    """An export carries everything unless the top rows were asked for."""

    def test_an_export_carries_every_sku_by_default(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="inventory")

        assert report["row_limit"] is None
        assert report["row_count"] == 3

    def test_top_only_records_the_choice(self, stocked: TestClient) -> None:
        """
        Recorded rather than inferred: an export silently capped at 50 is
        indistinguishable from a workspace that has 50 SKUs.
        """
        report = generate(stocked, kind="inventory", top_only=True)

        assert report["row_limit"] == 50

    def test_top_only_caps_the_rows(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="inventory", top_only=True)

        assert report["row_count"] <= 50

    def test_the_flag_defaults_to_off(self, stocked: TestClient) -> None:
        """Backward compatible: a request that never heard of it exports all."""
        response = stocked.post(REPORTS, json={"kind": "inventory", "fmt": "csv"})

        assert response.json()["row_limit"] is None


class TestLimitedReportsNeverRestateTotals:
    """A limited report says what it is showing. It never rewrites the totals.

    ``_sku_rows`` applies the limit in SQL ordered by sales descending, so
    unmatched SKUs -- which have zero sales -- sit at the bottom and a Top 50
    page contained none of them. Counting that page produced "0 of 1641 imported
    SKUs had no Shopify sale" while 77 of them had.
    """

    def test_the_unmatched_count_comes_from_every_sku(self, stocked: TestClient) -> None:
        full = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()
        # Derived from the rows rather than hard-coded, so the assertion is
        # about the relationship and not about this fixture's arithmetic.
        unmatched = sum(1 for row in full["rows"] if row[4] == "Unmatched")

        assert f"{unmatched} of {len(full['rows'])} imported SKUs" in full["subtitle"]

    def test_limiting_the_rows_does_not_change_the_statistic(self, stocked: TestClient) -> None:
        full = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()
        unmatched = sum(1 for row in full["rows"] if row[4] == "Unmatched")
        total = len(full["rows"])

        limited = stocked.get(PREVIEW, params={"kind": "sku_matching", "limit": 1}).json()

        assert len(limited["rows"]) == 1
        # The global fact survives the limit; the page size is stated separately.
        assert f"{unmatched} of {total} imported SKUs" in limited["subtitle"]
        assert f"Showing 1 of {total} SKUs" in limited["subtitle"]

    def test_a_limited_page_never_reports_zero_unmatched(self, stocked: TestClient) -> None:
        """The exact false sentence this replaced."""
        limited = stocked.get(PREVIEW, params={"kind": "sku_matching", "limit": 1}).json()

        assert "0 of" not in limited["subtitle"]

    def test_unmatched_skus_survive_the_limit(self, stocked: TestClient) -> None:
        """Unmatched sort first, so a one-row page is the unmatched one."""
        limited = stocked.get(PREVIEW, params={"kind": "sku_matching", "limit": 1}).json()

        assert limited["rows"][0][4] == "Unmatched"

    def test_an_unlimited_report_states_the_plain_total(self, stocked: TestClient) -> None:
        full = stocked.get(PREVIEW, params={"kind": "sku_matching"}).json()

        assert "Showing" not in full["subtitle"]

    def test_the_per_sku_reports_say_when_they_are_truncated(self, stocked: TestClient) -> None:
        """A report that silently stops reads as a workspace that small."""
        limited = stocked.get(PREVIEW, params={"kind": "inventory", "limit": 1}).json()

        assert "Showing top 1 of 3 SKUs by Shopify sales" in limited["subtitle"]


class TestDownload:
    def test_csv_comes_back_as_an_attachment(self, stocked: TestClient) -> None:
        report = generate(stocked, fmt="csv")

        response = stocked.get(f"{REPORTS}/{report['id']}/download")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert report["filename"] in response.headers["content-disposition"]
        assert response.content.startswith(b"\xef\xbb\xbf")

    def test_xlsx_downloads_as_a_real_workbook(self, stocked: TestClient) -> None:
        report = generate(stocked, fmt="xlsx")

        response = stocked.get(f"{REPORTS}/{report['id']}/download")

        assert "spreadsheetml" in response.headers["content-type"]
        assert "xl/workbook.xml" in zipfile.ZipFile(io.BytesIO(response.content)).namelist()

    def test_pdf_downloads_as_a_pdf(self, stocked: TestClient) -> None:
        report = generate(stocked, fmt="pdf")

        response = stocked.get(f"{REPORTS}/{report['id']}/download")

        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_the_download_contains_the_imported_rows(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="inventory", fmt="csv")

        text = stocked.get(f"{REPORTS}/{report['id']}/download").content.decode("utf-8-sig")

        assert "DD-1001" in text
        # The sheet's own columns; product name is not one of them any more.
        assert "Total Complaints" in text

    def test_a_missing_report_is_a_404(self, stocked: TestClient) -> None:
        response = stocked.get(f"{REPORTS}/9999/download")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "report_not_found"

    def test_a_failed_report_cannot_be_downloaded(
        self, stocked: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("nope")

        monkeypatch.setattr(reports_service.report_files, "render", boom)
        report = stocked.post(REPORTS, json={"kind": "inventory", "fmt": "csv"}).json()

        response = stocked.get(f"{REPORTS}/{report['id']}/download")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "report_not_ready"


class TestExportCentre:
    def test_reports_are_listed_newest_first(self, stocked: TestClient) -> None:
        generate(stocked, kind="inventory")
        second = generate(stocked, kind="sales")

        body = stocked.get(REPORTS).json()

        assert body["total"] == 2
        assert body["items"][0]["id"] == second["id"]

    def test_the_list_never_carries_the_file(self, stocked: TestClient) -> None:
        """A megabyte per row to print a filename would make the list crawl."""
        generate(stocked, fmt="xlsx")

        body = stocked.get(REPORTS).json()

        assert "content" not in body["items"][0]

    def test_filtering_by_kind(self, stocked: TestClient) -> None:
        generate(stocked, kind="inventory")
        generate(stocked, kind="sales")

        body = stocked.get(REPORTS, params={"kind": "sales"}).json()

        assert body["total"] == 1
        assert body["items"][0]["kind"] == "sales"

    def test_pagination(self, stocked: TestClient) -> None:
        for _ in range(3):
            generate(stocked)

        body = stocked.get(REPORTS, params={"limit": 2}).json()

        assert body["total"] == 3
        assert len(body["items"]) == 2

    def test_one_report_can_be_polled_for_its_status(self, stocked: TestClient) -> None:
        report = generate(stocked)

        body = stocked.get(f"{REPORTS}/{report['id']}").json()

        assert body["id"] == report["id"]
        assert body["status"] == "ready"

    def test_polling_an_unknown_id_is_a_404(self, stocked: TestClient) -> None:
        assert stocked.get(f"{REPORTS}/4242").status_code == 404


class TestDelete:
    def test_a_report_can_be_deleted(self, stocked: TestClient) -> None:
        report = generate(stocked)

        assert stocked.delete(f"{REPORTS}/{report['id']}").status_code == 204
        assert stocked.get(REPORTS).json()["total"] == 0

    def test_deleting_removes_the_bytes_too(self, stocked: TestClient) -> None:
        report = generate(stocked, fmt="xlsx")

        stocked.delete(f"{REPORTS}/{report['id']}")

        with session_module.get_session_factory()() as db:
            assert db.scalars(select(Report)).all() == []

    def test_a_deleted_report_cannot_be_downloaded(self, stocked: TestClient) -> None:
        report = generate(stocked)
        stocked.delete(f"{REPORTS}/{report['id']}")

        assert stocked.get(f"{REPORTS}/{report['id']}/download").status_code == 404

    def test_deleting_twice_is_a_404_not_a_500(self, stocked: TestClient) -> None:
        report = generate(stocked)
        stocked.delete(f"{REPORTS}/{report['id']}")

        assert stocked.delete(f"{REPORTS}/{report['id']}").status_code == 404


class TestReclaim:
    def test_a_report_left_preparing_is_failed_on_start_up(self, stocked: TestClient) -> None:
        """The worker is a thread: nothing preparing at exit is still preparing."""
        generate(stocked)
        with session_module.get_session_factory()() as db:
            stuck = db.scalars(select(Report)).one()
            stuck.status = "preparing"
            db.commit()

            assert reports_service.reclaim_interrupted(db) == 1

        body = stocked.get(REPORTS).json()["items"][0]
        assert body["status"] == "failed"
        assert body["error_code"] == "report_interrupted"

    def test_ready_reports_are_left_alone(self, stocked: TestClient) -> None:
        generate(stocked)

        with session_module.get_session_factory()() as db:
            assert reports_service.reclaim_interrupted(db) == 0


class TestHistoryLimit:
    def test_the_oldest_reports_are_pruned(
        self, stocked: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a cap the table grows for ever, each row holding its bytes."""
        monkeypatch.setattr(reports_service, "HISTORY_LIMIT", 3)

        for _ in range(5):
            generate(stocked)

        assert stocked.get(REPORTS).json()["total"] == 3


class TestSnapshot:
    def test_a_report_does_not_change_when_the_data_does(self, stocked: TestClient) -> None:
        """ "The export I sent you on Tuesday" has to still mean something."""
        report = generate(stocked, kind="inventory", fmt="csv")
        before = stocked.get(f"{REPORTS}/{report['id']}/download").content

        import_stock(stocked, [("DD-9999", "Brand New Thing", 42)])
        after = stocked.get(f"{REPORTS}/{report['id']}/download").content

        assert after == before
        assert b"DD-9999" not in after

    def test_a_second_request_makes_a_second_report(self, stocked: TestClient) -> None:
        first = generate(stocked, kind="inventory")
        second = generate(stocked, kind="inventory")

        assert first["id"] != second["id"]
        assert stocked.get(REPORTS).json()["total"] == 2


class TestQueryEfficiency:
    """Guards the fixes for the two problems the M6 review measured."""

    def test_the_sales_report_does_not_scale_queries_with_rows(self, signed_in: TestClient) -> None:
        """Was one query per row: 12,914 statements and 4.9 s on the real store.

        The count is asserted as a ceiling rather than an exact number so that
        an unrelated extra query does not fail the suite — but a return of the
        per-row lookup would blow straight past it.
        """
        import_stock(signed_in, [(f"DD-{i:04d}", f"Item {i}", 5) for i in range(60)])
        sell(signed_in, [(f"DD-{i:04d}", 2, 1000, 1) for i in range(60)])
        rebuild(signed_in)

        from sqlalchemy import event

        from app.db.session import get_engine

        statements: list[str] = []

        def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
            statements.append(statement)

        event.listen(get_engine(), "before_cursor_execute", record)
        try:
            with session_module.get_session_factory()() as db:
                from app.services import report_data

                table = report_data.build(db, kind="sales", workspace_id=1, days=30)
        finally:
            event.remove(get_engine(), "before_cursor_execute", record)

        assert len(table.rows) == 60
        assert len(statements) < 15, f"{len(statements)} statements for 60 rows"


class TestPreviewCost:
    def test_the_preview_limit_reaches_the_query(self, stocked: TestClient) -> None:
        """Was: build the whole report, then slice. A 12-row preview cost a full export."""
        from app.services import report_data

        with session_module.get_session_factory()() as db:
            table = report_data.build(db, kind="inventory", workspace_id=1, days=30, limit=2)

        assert len(table.rows) == 2

    def test_a_limited_preview_still_reports_the_true_total(self, stocked: TestClient) -> None:
        """Truncation has to be counted, not inferred from the capped row list."""
        body = stocked.get(PREVIEW, params={"kind": "inventory", "limit": 1}).json()

        assert len(body["rows"]) == 1
        assert body["truncated"] is True
        assert "3 SKUs" in body["subtitle"]

    def test_a_limited_sales_preview_counts_every_sheet_sku(self, stocked: TestClient) -> None:
        """The sales report lists sheet SKUs with their Shopify sales, so the
        total is the sheet's row count, not the number that happened to sell."""
        sell(stocked, [("DD-1001", 5, 1000, 1), ("DD-1002", 3, 1000, 1)])
        rebuild(stocked)

        body = stocked.get(PREVIEW, params={"kind": "sales", "limit": 1}).json()

        assert len(body["rows"]) == 1
        assert body["truncated"] is True
        assert "3 SKUs" in body["subtitle"]

    def test_an_unlimited_build_is_not_truncated(self, stocked: TestClient) -> None:
        body = stocked.get(PREVIEW, params={"kind": "inventory", "limit": 50}).json()

        assert body["truncated"] is False


class TestFilenames:
    def test_two_reports_made_together_get_different_names(self, stocked: TestClient) -> None:
        """A second-resolution stamp collided; the row id cannot."""
        first = generate(stocked, kind="inventory", fmt="csv")
        second = generate(stocked, kind="inventory", fmt="csv")

        assert first["filename"] != second["filename"]

    def test_the_name_carries_the_report_id(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="sales", fmt="xlsx")

        assert report["filename"].endswith(f"-{report['id']}.xlsx")

    def test_the_name_still_sorts_chronologically(self, stocked: TestClient) -> None:
        report = generate(stocked)

        assert report["filename"].startswith("stocksync-inventory-20")

    def test_names_stay_filesystem_safe(self, stocked: TestClient) -> None:
        report = generate(stocked, kind="sku_performance", fmt="pdf")

        assert not set(report["filename"]) & set(r'<>:"/\|?* ')
