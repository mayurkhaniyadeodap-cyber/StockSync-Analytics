"""Both upload formats, through the whole stack.

The requirement is that a file may or may not carry a "Complaint Date" column
and the system supports either without being told which. What is asserted here
is the consequence of that: a dated import answers the date range, an undated
one keeps the totals it stated, a workspace holding both does each correctly,
and every surface — the Dashboard card, the Dashboard table, SKU Performance,
the Analytics lenses and the exported files — reports the same number for the
same SKU.

The paths are genuinely separate in the code (``services.analytics`` for the
Dashboard, ``repositories.analytics`` → ``services.insights`` for Analytics), so
"they agree" is a property that has to be tested rather than assumed. It is the
same pair that drifted over Shopify Sales %.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_analytics_api import rebuild, sell

KPIS = "/api/analytics/kpis"
SKUS = "/api/analytics/skus"
INSIGHTS = "/api/analytics/insights"
PERFORMANCE = "/api/analytics/performance"


def upload(client: TestClient, body: str, name: str = "sheet.csv") -> None:
    response = client.post(
        "/api/imports/upload",
        files={"file": (name, io.BytesIO(body.encode()), "text/csv")},
    )
    assert response.status_code == 200, response.text


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def dated(client: TestClient, *skus: tuple[str, int, int]) -> None:
    """A complaint export, as one file: ``(sku, days_back, rows)`` per entry.

    Every SKU goes in the same upload because an import now states the whole
    dataset — two files would mean the second replacing the first, which is a
    different test from the ones below.
    """
    body = "Complaint Date,Order No,SKU Code,Reason\n" + "".join(
        f"{days_ago(days_back)},ORD-{sku}-{i},{sku},Missing\n"
        for sku, days_back, rows in skus
        for i in range(rows)
    )
    upload(client, body, "complaints.csv")


def aggregated(client: TestClient, *skus: tuple[str, int]) -> None:
    """The other format, also one file: ``(sku, complaints)`` per entry."""
    body = "SKU,Total Qty.,Missing\n" + "".join(f"{sku},100,{missing}\n" for sku, missing in skus)
    upload(client, body, "stock.csv")


def part_dated(client: TestClient, *, dated_sku: str, undated_sku: str, rows: int = 1) -> None:
    """One complaint export where only some rows carry a readable date.

    **The only way a workspace is mixed** now that an import replaces the whole
    dataset: two files cannot coexist, but two kinds of row in one file can.
    ``undated_sku`` reaches ``inventory_items`` and never ``sku_daily_complaints``,
    so it has no dated record and its total stands in every window.
    """
    body = "Complaint Date,Order No,SKU Code,Reason\n"
    body += "".join(f"{days_ago(2)},D-{i},{dated_sku},Missing\n" for i in range(rows))
    body += "".join(f",U-{i},{undated_sku},Damage\n" for i in range(rows))
    upload(client, body, "complaints.csv")


class TestTheColumnIsDetectedRatherThanConfigured:
    def test_a_file_with_dates_is_stored_with_them(self, signed_in: TestClient) -> None:
        dated(signed_in, ("DD-1", 3, 4))

        scope = signed_in.get(f"{PERFORMANCE}?days=30").json()["complaint_scope"]

        assert scope["filtered_by_date"] is True
        assert scope["dated_skus"] == 1
        assert scope["undated_complaints"] == 0

    def test_a_file_without_dates_is_accepted_just_the_same(self, signed_in: TestClient) -> None:
        """No error, no prompt, no configuration — it simply imports."""
        aggregated(signed_in, ("DD-1", 6))

        body = signed_in.get(f"{PERFORMANCE}?days=30").json()

        assert body["rows"][0]["total_complaints"] == 6
        assert body["complaint_scope"]["filtered_by_date"] is False

    def test_the_header_may_be_spelled_however_the_export_spells_it(
        self, signed_in: TestClient
    ) -> None:
        """`Complaint Date` and a bare `Date` are the same column."""
        upload(
            signed_in,
            f"Date,Order No,SKU Code,Reason\n{days_ago(3)},O-1,DD-9,Missing\n",
            "complaints.csv",
        )

        assert signed_in.get(f"{PERFORMANCE}?days=30").json()["rows"][0]["total_complaints"] == 1
        assert signed_in.get(f"{PERFORMANCE}?days=1").json()["rows"][0]["total_complaints"] == 0


class TestADatedImportAnswersTheRange:
    @pytest.fixture
    def store(self, signed_in: TestClient) -> TestClient:
        # One file: 3 complaints inside 30 days, 5 outside.
        dated(signed_in, ("DD-1", 2, 3), ("DD-2", 100, 5))
        return signed_in

    def test_a_narrow_window_selects_only_what_falls_in_it(self, store: TestClient) -> None:
        rows = {r["sku"]: r for r in store.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"]}

        assert rows["DD-1"]["total_complaints"] == 3
        assert rows["DD-2"]["total_complaints"] == 0

    def test_a_wide_window_selects_both(self, store: TestClient) -> None:
        rows = {r["sku"]: r for r in store.get(f"{PERFORMANCE}?days=365&limit=50").json()["rows"]}

        assert rows["DD-1"]["total_complaints"] == 3
        assert rows["DD-2"]["total_complaints"] == 5

    def test_a_custom_range_works_the_same_way(self, store: TestClient) -> None:
        today = date.today()
        window = f"since={today - timedelta(days=120)}&until={today - timedelta(days=90)}&limit=50"

        rows = {r["sku"]: r for r in store.get(f"{PERFORMANCE}?{window}").json()["rows"]}

        assert rows["DD-1"]["total_complaints"] == 0
        assert rows["DD-2"]["total_complaints"] == 5

    def test_the_category_breakdown_is_windowed_too(self, store: TestClient) -> None:
        """Not just the total — the ten columns beside it move together."""
        rows = {r["sku"]: r for r in store.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"]}

        assert rows["DD-1"]["complaints"]["missing"] == 3
        assert rows["DD-2"]["complaints"]["missing"] == 0

    def test_nothing_is_unaccounted_for_so_the_page_has_no_caveat(self, store: TestClient) -> None:
        """Every complaint here carries a date, so none of them is unfilterable
        and the client has nothing to explain."""
        scope = store.get(f"{PERFORMANCE}?days=1").json()["complaint_scope"]

        assert scope["undated_complaints"] == 0
        assert scope["undated_skus"] == 0


class TestAnUndatedImportKeepsItsTotals:
    @pytest.fixture
    def store(self, signed_in: TestClient) -> TestClient:
        aggregated(signed_in, ("DD-1", 7))
        sell(signed_in, [("DD-1", 40, 19900, 2)])
        rebuild(signed_in)
        return signed_in

    @pytest.mark.parametrize("days", [1, 30, 365])
    def test_the_total_is_the_same_in_every_range(self, store: TestClient, days: int) -> None:
        body = store.get(f"{PERFORMANCE}?days={days}&limit=50").json()

        assert body["rows"][0]["total_complaints"] == 7

    def test_only_the_shopify_figure_moves(self, store: TestClient) -> None:
        """The requirement, exactly: sales follow the range and complaints do not."""
        wide = store.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"][0]
        narrow = store.get(f"{PERFORMANCE}?days=1&limit=50").json()["rows"][0]

        assert wide["shopify_sales"] == 40
        assert narrow["shopify_sales"] == 0
        assert wide["total_complaints"] == narrow["total_complaints"] == 7

    def test_the_scope_says_nothing_is_filtered(self, store: TestClient) -> None:
        """The counts the client words its sentence from. The wording itself is
        the client's — see ComplaintScopeNote."""
        scope = store.get(f"{PERFORMANCE}?days=30").json()["complaint_scope"]

        assert scope["filtered_by_date"] is False
        assert scope["dated_skus"] == 0
        assert scope["undated_skus"] == 1
        assert scope["undated_complaints"] == 7

    def test_the_payload_carries_no_sentence(self, store: TestClient) -> None:
        """`note` was removed: a string the client never rendered went stale,
        claiming no Complaint Date column on a workspace that had 308 dated
        SKUs. The counts are the contract now."""
        scope = store.get(f"{PERFORMANCE}?days=30").json()["complaint_scope"]

        assert "note" not in scope
        assert set(scope) == {
            "filtered_by_date",
            "dated_skus",
            "undated_skus",
            "undated_complaints",
        }

    def test_a_sku_with_no_complaints_does_not_raise_the_note(self, signed_in: TestClient) -> None:
        """Nothing to be undated about, so nothing to explain."""
        aggregated(signed_in, ("DD-CLEAN", 0))

        scope = signed_in.get(f"{PERFORMANCE}?days=30").json()["complaint_scope"]

        assert scope["undated_skus"] == 0
        assert scope["undated_complaints"] == 0


class TestOneFileMayHoldBoth:
    """A single export where some rows carry a date and some do not.

    Two *files* can no longer coexist — an import replaces the dataset — so this
    is what "mixed" now means, and it is still decided per SKU: DD-DATED answers
    the range, DD-UNDATED reports the total the file stated.
    """

    @pytest.fixture
    def mixed(self, signed_in: TestClient) -> TestClient:
        part_dated(signed_in, dated_sku="DD-DATED", undated_sku="DD-UNDATED", rows=4)
        return signed_in

    def test_each_sku_is_treated_the_way_its_own_rows_allow(self, mixed: TestClient) -> None:
        wide = {r["sku"]: r for r in mixed.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"]}
        narrow = {r["sku"]: r for r in mixed.get(f"{PERFORMANCE}?days=1&limit=50").json()["rows"]}

        assert wide["DD-UNDATED"]["total_complaints"] == 4
        assert wide["DD-DATED"]["total_complaints"] == 4
        # Narrowing drops the dated SKU's complaints and leaves the other alone.
        assert narrow["DD-UNDATED"]["total_complaints"] == 4
        assert narrow["DD-DATED"]["total_complaints"] == 0

    def test_the_scope_counts_both_kinds(self, mixed: TestClient) -> None:
        scope = mixed.get(f"{PERFORMANCE}?days=30").json()["complaint_scope"]

        assert scope["dated_skus"] == 1
        assert scope["undated_skus"] == 1
        assert scope["undated_complaints"] == 4
        assert scope["filtered_by_date"] is True

    def test_the_card_is_the_sum_of_the_column_beneath_it(self, mixed: TestClient) -> None:
        card = mixed.get(f"{KPIS}?days=30").json()["total_complaints"]
        rows = mixed.get(f"{PERFORMANCE}?days=30&limit=200").json()["rows"]

        assert card == sum(r["total_complaints"] for r in rows) == 8


class TestEverySurfaceReportsTheSameNumber:
    """Five read paths, two of them written independently of each other."""

    @pytest.fixture
    def mixed(self, signed_in: TestClient) -> TestClient:
        part_dated(signed_in, dated_sku="DD-DATED", undated_sku="DD-UNDATED", rows=4)
        sell(signed_in, [("DD-UNDATED", 10, 19900, 1)])
        rebuild(signed_in)
        return signed_in

    @pytest.mark.parametrize("days", [1, 30, 365])
    def test_the_dashboard_and_analytics_cards_agree(self, mixed: TestClient, days: int) -> None:
        dashboard = mixed.get(f"{KPIS}?days={days}").json()["total_complaints"]
        analytics = mixed.get(f"{INSIGHTS}?days={days}").json()["kpis"]["total_complaints"]

        assert dashboard == analytics

    @pytest.mark.parametrize("days", [1, 30, 365])
    def test_the_dashboard_table_and_sku_performance_agree(
        self, mixed: TestClient, days: int
    ) -> None:
        """Different modules, different queries, one number per SKU."""
        dashboard = {
            r["sku"]: r["total_complaints"]
            for r in mixed.get(f"{SKUS}?days={days}&limit=200").json()["rows"]
        }
        page = {
            r["sku"]: r["total_complaints"]
            for r in mixed.get(f"{PERFORMANCE}?days={days}&limit=200").json()["rows"]
        }

        assert dashboard == page

    @pytest.mark.parametrize("days", [1, 30, 365])
    def test_every_payload_carries_the_same_scope(self, mixed: TestClient, days: int) -> None:
        scopes = [
            mixed.get(f"{KPIS}?days={days}").json()["complaint_scope"],
            mixed.get(f"{SKUS}?days={days}").json()["complaint_scope"],
            mixed.get(f"{INSIGHTS}?days={days}").json()["complaint_scope"],
            mixed.get(f"{PERFORMANCE}?days={days}").json()["complaint_scope"],
        ]

        assert all(scope == scopes[0] for scope in scopes), scopes

    def test_the_complaint_lens_agrees_with_the_card(self, mixed: TestClient) -> None:
        body = mixed.get(f"{INSIGHTS}?days=30").json()

        assert body["complaints"]["total_complaints"] == body["kpis"]["total_complaints"]

    @pytest.mark.parametrize("days", [1, 30, 365])
    def test_the_exported_file_matches_the_screen(self, mixed: TestClient, days: int) -> None:
        from app.db import session as session_module
        from app.services import report_data

        on_screen = {
            r["sku"]: r["total_complaints"]
            for r in mixed.get(f"{PERFORMANCE}?days={days}&limit=200").json()["rows"]
        }
        with session_module.get_session_factory()() as db:
            report = report_data.sku_performance_report(db, workspace_id=1, days=days, limit=200)
        at = {column.header: index for index, column in enumerate(report.columns)}

        in_file = {row[0]: int(row[at["Complaints"]]) for row in report.rows}
        assert in_file == on_screen


class TestTheSourceIsStillChosenPerSku:
    """Per SKU, within the one file that is now the whole dataset.

    This class used to import an aggregated sheet and then a dated export, and
    assert that the second won for the SKUs it named and left the rest alone.
    That shape no longer exists: an import replaces the dataset, so the second
    file's SKUs are the only ones there is anything to decide about. What
    survives — and is what mattered — is that the decision is made per SKU
    rather than per workspace, which one file with two kinds of row still tests.
    """

    @pytest.fixture
    def workspace(self, signed_in: TestClient) -> TestClient:
        # One export: DD-A dated twice, DD-B and DD-C on rows with no date.
        upload(
            signed_in,
            "Complaint Date,Order No,SKU Code,Reason\n"
            f"{days_ago(2)},O-1,DD-A,Missing\n"
            f"{days_ago(2)},O-2,DD-A,Missing\n"
            ",O-3,DD-B,Damage\n"
            ",O-4,DD-C,Damage\n",
            "complaints.csv",
        )
        return signed_in

    def rows(self, client: TestClient, days: int) -> dict[str, int]:
        return {
            r["sku"]: r["total_complaints"]
            for r in client.get(f"{PERFORMANCE}?days={days}&limit=50").json()["rows"]
        }

    def test_the_dated_sku_answers_the_range(self, workspace: TestClient) -> None:
        assert self.rows(workspace, 1)["DD-A"] == 0
        assert self.rows(workspace, 30)["DD-A"] == 2

    def test_the_undated_skus_report_the_same_figure_in_every_range(
        self, workspace: TestClient
    ) -> None:
        """No dated record, so no window can select them — and they are not
        hidden because of it."""
        for days in (1, 30, 365):
            rows = self.rows(workspace, days)
            assert rows["DD-B"] == 1, days
            assert rows["DD-C"] == 1, days

    def test_a_dated_sku_contributes_nothing_to_the_undated_tally(
        self, workspace: TestClient
    ) -> None:
        """Aggregated complaints are never used for a dated SKU — not for its
        figure, and not for the count behind the note."""
        scope = workspace.get(f"{PERFORMANCE}?days=30").json()["complaint_scope"]

        assert scope["dated_skus"] == 1
        assert scope["undated_skus"] == 2
        assert scope["undated_complaints"] == 2  # DD-B and DD-C only

    def test_a_blank_date_cell_does_not_pull_the_aggregate_back_in(
        self, signed_in: TestClient
    ) -> None:
        """Two of the live workspace's complaints came in on rows whose date cell
        was unreadable. Counting the difference kept the note on screen for a
        fully dated import, so a dated SKU now contributes nothing regardless."""
        upload(
            signed_in,
            "Complaint Date,Order No,SKU Code,Reason\n"
            f"{days_ago(2)},O-1,DD-A,Missing\n"
            ",O-2,DD-A,Missing\n",  # no date on this one
            "complaints.csv",
        )

        body = signed_in.get(f"{PERFORMANCE}?days=30").json()

        assert body["rows"][0]["total_complaints"] == 1  # only the dated row
        assert body["complaint_scope"]["undated_complaints"] == 0

    def test_every_surface_agrees(self, workspace: TestClient) -> None:
        dashboard = {
            r["sku"]: r["total_complaints"]
            for r in workspace.get(f"{SKUS}?days=30&limit=50").json()["rows"]
        }

        assert dashboard == self.rows(workspace, 30) == {"DD-A": 2, "DD-B": 1, "DD-C": 1}
        assert workspace.get(f"{KPIS}?days=30").json()["total_complaints"] == 4


class TestAnImportReplacesTheDataset:
    """The latest successful import is the whole dataset. Nothing accumulates.

    Before this, an import wrote the SKUs it named and left every other row
    alone. A 1,372-row spreadsheet followed by a 309-row export left 1,641 SKUs
    on screen, 1,332 of them carrying complaint totals from a file nobody had
    looked at in weeks — and no way to tell which was which from the outside.
    """

    def skus(self, client: TestClient) -> set[str]:
        return {r["sku"] for r in client.get(f"{PERFORMANCE}?days=365&limit=200").json()["rows"]}

    def test_a_smaller_file_shrinks_the_workspace_to_its_size(self, signed_in: TestClient) -> None:
        """The requirement, literally: import 3 SKUs, see 3 SKUs."""
        aggregated(signed_in, ("DD-A", 1), ("DD-B", 2), ("DD-C", 3), ("DD-D", 4), ("DD-E", 5))
        assert len(self.skus(signed_in)) == 5

        aggregated(signed_in, ("DD-A", 1), ("DD-B", 2), ("DD-Z", 9))

        assert self.skus(signed_in) == {"DD-A", "DD-B", "DD-Z"}

    def test_the_removed_skus_take_their_complaints_with_them(self, signed_in: TestClient) -> None:
        """The point of the change: an old file's complaints must stop counting."""
        aggregated(signed_in, ("DD-A", 10), ("DD-B", 20), ("DD-C", 30))
        assert signed_in.get(f"{KPIS}?days=30").json()["total_complaints"] == 60

        aggregated(signed_in, ("DD-A", 10))

        assert signed_in.get(f"{KPIS}?days=30").json()["total_complaints"] == 10

    def test_the_response_says_how_many_went(self, signed_in: TestClient) -> None:
        """A count dropping from five to two should never be a surprise."""
        aggregated(signed_in, ("DD-A", 1), ("DD-B", 2), ("DD-C", 3), ("DD-D", 4), ("DD-E", 5))

        body = signed_in.post(
            "/api/imports/upload",
            files={
                "file": (
                    "stock.csv",
                    io.BytesIO(b"SKU,Total Qty.,Missing\nDD-A,100,1\nDD-Z,100,9\n"),
                    "text/csv",
                )
            },
        ).json()

        assert body["items_created"] == 1  # DD-Z
        assert body["items_updated"] == 1  # DD-A
        assert body["items_removed"] == 4  # DD-B, DD-C, DD-D, DD-E

    def test_switching_format_replaces_the_dated_records_too(self, signed_in: TestClient) -> None:
        dated(signed_in, ("DD-A", 2, 3), ("DD-B", 2, 3))
        assert signed_in.get(f"{KPIS}?days=30").json()["total_complaints"] == 6

        aggregated(signed_in, ("DD-C", 4))

        body = signed_in.get(f"{PERFORMANCE}?days=30&limit=50").json()
        assert {r["sku"] for r in body["rows"]} == {"DD-C"}
        assert body["complaint_scope"]["dated_skus"] == 0

    def test_import_history_keeps_every_batch(self, signed_in: TestClient) -> None:
        """Data is replaced; the audit trail is not."""
        aggregated(signed_in, ("DD-A", 1))
        aggregated(signed_in, ("DD-B", 2))
        dated(signed_in, ("DD-C", 2, 1))

        history = signed_in.get("/api/imports?limit=50").json()

        assert history["total"] == 3
        assert [b["status"] for b in history["items"]] == ["complete"] * 3


class TestAFailedImportKeepsThePreviousDataset:
    """Replacement is not deletion-on-attempt. Only a *successful* import wins."""

    def test_an_unreadable_file_changes_nothing(self, signed_in: TestClient) -> None:
        aggregated(signed_in, ("DD-A", 7), ("DD-B", 8))

        response = signed_in.post(
            "/api/imports/upload",
            files={"file": ("stock.csv", io.BytesIO(b"Nothing,Useful\n1,2\n"), "text/csv")},
        )

        assert response.status_code == 422
        rows = signed_in.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"]
        assert {r["sku"]: r["total_complaints"] for r in rows} == {"DD-A": 7, "DD-B": 8}

    def test_a_file_whose_every_row_is_rejected_changes_nothing(
        self, signed_in: TestClient
    ) -> None:
        """It parses, so it gets past the reader — and still must not wipe."""
        aggregated(signed_in, ("DD-A", 7), ("DD-B", 8))

        response = signed_in.post(
            "/api/imports/upload",
            files={"file": ("stock.csv", io.BytesIO(b"SKU,Total Qty.\n,10\n,20\n"), "text/csv")},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "no_usable_rows"
        assert len(signed_in.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"]) == 2

    def test_the_failure_is_still_recorded_in_history(self, signed_in: TestClient) -> None:
        aggregated(signed_in, ("DD-A", 7))
        signed_in.post(
            "/api/imports/upload",
            files={"file": ("bad.csv", io.BytesIO(b"Nothing,Useful\n1,2\n"), "text/csv")},
        )

        history = signed_in.get("/api/imports?limit=50").json()["items"]

        assert history[0]["status"] == "failed"
        assert history[0]["origin_filename"] == "bad.csv"


class TestShopifyIsUntouched:
    """Only imported inventory and complaints are replaced."""

    def test_sales_survive_an_import_that_removes_their_sku(self, signed_in: TestClient) -> None:
        """The rollup is built from orders, not from the sheet, so re-importing
        without a SKU hides its row — and the sale is still there when the SKU
        comes back."""
        aggregated(signed_in, ("DD-A", 1))
        sell(signed_in, [("DD-A", 25, 19900, 2)])
        rebuild(signed_in)
        assert signed_in.get(f"{KPIS}?days=30").json()["shopify_sales"] == 25

        aggregated(signed_in, ("DD-B", 1))
        assert signed_in.get(f"{KPIS}?days=30").json()["shopify_sales"] == 0

        aggregated(signed_in, ("DD-A", 1))
        assert signed_in.get(f"{KPIS}?days=30").json()["shopify_sales"] == 25

    def test_the_store_total_never_moves(self, signed_in: TestClient) -> None:
        """`shopify_sales_all` counts what the store sold, sheet or no sheet."""
        aggregated(signed_in, ("DD-A", 1))
        sell(signed_in, [("DD-A", 25, 19900, 2), ("DD-NEVER", 75, 19900, 2)])
        rebuild(signed_in)

        before = signed_in.get(f"{KPIS}?days=30").json()["shopify_sales_all"]
        aggregated(signed_in, ("DD-B", 1))
        after = signed_in.get(f"{KPIS}?days=30").json()["shopify_sales_all"]

        assert before == after == 100


class TestReimporting:
    def test_a_stock_sheet_does_not_take_a_dated_sku_back_to_aggregated(
        self, signed_in: TestClient
    ) -> None:
        """A stock sheet cannot undate a SKU, because it does not own that record.

        This asserted the opposite until the import scoping was fixed: an
        aggregated sheet cleared every dated row, so the answer switched to its
        own lifetime total. That is what made the two file types destroy each
        other on the live workspace.

        Now the dated rows survive, and the per-SKU rule in
        ``repositories.complaints`` — dated wins, absolutely — keeps DD-1 on its
        dated record. A one-day window therefore still reports 0, because its three
        complaints are two days back.
        """
        dated(signed_in, ("DD-1", 2, 3))
        assert signed_in.get(f"{PERFORMANCE}?days=1").json()["rows"][0]["total_complaints"] == 0

        aggregated(signed_in, ("DD-1", 8))

        body = signed_in.get(f"{PERFORMANCE}?days=1").json()
        assert body["rows"][0]["total_complaints"] == 0
        assert body["complaint_scope"]["dated_skus"] == 1
        # Its aggregated column is not consulted, so it contributes nothing here.
        assert body["complaint_scope"]["undated_complaints"] == 0

        # And the range still reaches them: the dated record is intact.
        wide = signed_in.get(f"{PERFORMANCE}?days=30").json()
        assert wide["rows"][0]["total_complaints"] == 3

    def test_a_new_complaint_export_is_how_a_dated_sku_is_restated(
        self, signed_in: TestClient
    ) -> None:
        """The way back is another export, which owns the table it replaces."""
        dated(signed_in, ("DD-1", 2, 3))
        dated(signed_in, ("DD-1", 0, 5))  # five, today

        body = signed_in.get(f"{PERFORMANCE}?days=1").json()
        assert body["rows"][0]["total_complaints"] == 5

    def test_replacing_an_aggregated_file_with_a_dated_one_switches_back(
        self, signed_in: TestClient
    ) -> None:
        aggregated(signed_in, ("DD-1", 8))
        assert signed_in.get(f"{PERFORMANCE}?days=1").json()["rows"][0]["total_complaints"] == 8

        dated(signed_in, ("DD-1", 2, 3))

        body = signed_in.get(f"{PERFORMANCE}?days=1").json()
        assert body["rows"][0]["total_complaints"] == 0  # the three are 2 days back
        assert body["complaint_scope"]["undated_complaints"] == 0

    def test_re_importing_the_same_dated_file_does_not_double_it(
        self, signed_in: TestClient
    ) -> None:
        dated(signed_in, ("DD-1", 2, 3))
        dated(signed_in, ("DD-1", 2, 3))

        rows = signed_in.get(f"{PERFORMANCE}?days=30&limit=50").json()["rows"]
        assert rows[0]["total_complaints"] == 3
