"""The import workflow, end to end against a real database.

Every test drives a real endpoint against the ``signed_in`` client from
conftest, which is backed by a throwaway SQLite file. Nothing here stubs the
importer, the parser or the database: an import is the one operation in this
application that touches every layer at once, and a test that mocked the middle
of it would pass while the thing it describes was broken.

The network is the single exception. Google Sheet imports are stubbed at the
httpx transport — the address guard, redirect budget and size cap all still run,
so what is replaced is the internet, not the code under test.
"""

from __future__ import annotations

import io
import ipaddress
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.db import session as session_module
from app.models import (
    COMPLAINT_COLUMNS,
    ImportBatch,
    InventoryItem,
    LinkedSheet,
    SkuDailyComplaint,
)
from app.services import import_url

UPLOAD = "/api/imports/upload"
HISTORY = "/api/imports"
SHEETS = "/api/imports/sheets"
GOOGLE_SHEET = "/api/imports/google-sheet"
INVENTORY = "/api/inventory"
SUMMARY = "/api/inventory/summary"
PERFORMANCE = "/api/analytics/performance"
INSIGHTS = "/api/analytics/insights"

#: A public address, so the fetcher's internal-host guard is satisfied without
#: any real DNS. See ``serve``.
PUBLIC_IP = "93.184.216.34"

SHEET_URL = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit#gid=0"

#: Captured before anything is patched. serve may be called twice in one
#: test, and reading httpx.Client at call time would wrap the previous stub
#: in itself — which fails, because the stub already supplies a transport.
_REAL_HTTPX_CLIENT = httpx.Client


# ---------------------------------------------------------------------------
# uploading
# ---------------------------------------------------------------------------


def upload(client: TestClient, name: str, body: bytes, mime: str = "text/csv"):
    return client.post(UPLOAD, files={"file": (name, io.BytesIO(body), mime)})


def csv_upload(client: TestClient, text: str, name: str = "stock.csv"):
    return upload(client, name, text.encode("utf-8"))


def xlsx_upload(client: TestClient, rows: list[list[object]], name: str = "stock.xlsx"):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return upload(
        client,
        name,
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------------------------
# reading the database back
# ---------------------------------------------------------------------------


def items() -> list[InventoryItem]:
    with session_module.get_session_factory()() as db:
        return list(db.scalars(select(InventoryItem).order_by(InventoryItem.sku_normalized)))


def one_item(sku_normalized: str) -> InventoryItem:
    with session_module.get_session_factory()() as db:
        return db.scalars(
            select(InventoryItem).where(InventoryItem.sku_normalized == sku_normalized)
        ).one()


def batches() -> list[ImportBatch]:
    with session_module.get_session_factory()() as db:
        return list(db.scalars(select(ImportBatch).order_by(ImportBatch.id)))


def dated_complaints() -> list[SkuDailyComplaint]:
    with session_module.get_session_factory()() as db:
        return list(
            db.scalars(
                select(SkuDailyComplaint).order_by(
                    SkuDailyComplaint.sku_normalized, SkuDailyComplaint.complaint_date
                )
            )
        )


def linked_sheets() -> list[LinkedSheet]:
    with session_module.get_session_factory()() as db:
        return list(db.scalars(select(LinkedSheet).order_by(LinkedSheet.id)))


def complaint_total(item: InventoryItem) -> int:
    return sum(getattr(item, attribute) for attribute, _ in COMPLAINT_COLUMNS)


def days_ago(n: int) -> str:
    """A date ``n`` days back, written the ISO way.

    Relative rather than fixed, so a test that cares which side of a range a
    date falls on keeps meaning the same thing as the clock moves.
    """
    return (date.today() - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# the network, replaced at the transport
# ---------------------------------------------------------------------------


def serve(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[str]:
    """Answer sheet downloads from ``handler``. Returns the URLs requested.

    Installed under ``httpx.Client`` rather than over ``google_sheets.fetch``,
    so the redirect budget, size cap and address guard all still execute. DNS is
    stubbed alongside it: an IP literal still resolves to itself, so this cannot
    accidentally make 127.0.0.1 look public and disarm the guard.

    Safe to call twice in one test — to make a sheet readable, then to make it
    fail. The pristine client is captured at import, so the second call replaces
    the first stub rather than wrapping it.
    """
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    def fake_client(**kwargs: Any) -> httpx.Client:
        return _REAL_HTTPX_CLIENT(**kwargs, transport=httpx.MockTransport(record))

    monkeypatch.setattr(import_url.httpx, "Client", fake_client)

    def getaddrinfo(host: str, port: object, **kwargs: Any) -> list[Any]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            resolved = PUBLIC_IP
        else:
            resolved = host
        return [(2, 1, 6, "", (resolved, port or 443))]

    monkeypatch.setattr(import_url.socket, "getaddrinfo", getaddrinfo)
    return seen


def serving_csv(monkeypatch: pytest.MonkeyPatch, body: str) -> list[str]:
    """Every download returns this CSV."""
    return serve(
        monkeypatch,
        lambda _request: httpx.Response(
            200, headers={"content-type": "text/csv"}, content=body.encode()
        ),
    )


@pytest.fixture
def sheet_csv(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A readable Google Sheet with two SKUs on it."""
    return serving_csv(monkeypatch, "SKU,Total Qty.\nDD-1,10\nDD-2,20\n")


# ---------------------------------------------------------------------------


class TestAuthentication:
    """Every import endpoint sits behind the session."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", HISTORY),
            ("get", f"{HISTORY}/1"),
            ("get", SHEETS),
            ("get", INVENTORY),
            ("get", SUMMARY),
            ("post", SHEETS),
            ("post", GOOGLE_SHEET),
            ("delete", f"{SHEETS}/1"),
        ],
    )
    def test_it_needs_a_session(self, api: TestClient, method: str, path: str) -> None:
        assert getattr(api, method)(path).status_code == 401

    def test_upload_needs_a_session(self, api: TestClient) -> None:
        assert csv_upload(api, "SKU,Total Qty.\nDD-1,1\n").status_code == 401


class TestCsvImport:
    def test_it_stores_the_rows_and_reports_what_it_did(self, signed_in: TestClient) -> None:
        response = csv_upload(signed_in, "SKU,Product Name,Total Qty.\nDD-1,Fan,10\nDD-2,Bag,20\n")

        assert response.status_code == 200
        body = response.json()
        assert body["items_created"] == 2
        assert body["items_updated"] == 0
        assert body["batch"]["status"] == "complete"
        assert body["batch"]["rows_read"] == 2
        assert body["batch"]["rows_imported"] == 2
        assert [i.sku for i in items()] == ["DD-1", "DD-2"]

    def test_it_names_the_columns_it_found(self, signed_in: TestClient) -> None:
        """So a user whose headers were matched by alias can see what matched."""
        body = csv_upload(signed_in, "Item Code,Qty\nDD-1,10\n").json()

        assert body["detected_columns"]["sku"] == "Item Code"
        assert body["detected_columns"]["quantity"] == "Qty"
        assert body["header_row_number"] == 1

    def test_a_second_import_updates_rather_than_duplicating(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n")
        body = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,25\n").json()

        assert body["items_created"] == 0
        assert body["items_updated"] == 1
        assert len(items()) == 1
        assert one_item("dd1").total_qty == 25

    def test_the_newest_file_states_the_row_in_full(self, signed_in: TestClient) -> None:
        """An import replaces the dataset, so a column the new file omits is
        gone rather than inherited.

        The reverse of what this used to assert — "absent is not blank" kept the
        earlier product name — and the reversal is the point: a value that
        survives a file which does not mention it cannot be traced to any file.
        """
        csv_upload(signed_in, "SKU,Product Name,Total Qty.\nDD-1,Ceiling Fan,10\n")
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,12\n")

        item = one_item("dd1")
        assert item.product_name == ""
        assert item.total_qty == 12

    def test_the_header_may_sit_below_a_title_row(self, signed_in: TestClient) -> None:
        body = csv_upload(
            signed_in, "Stock Report\nGenerated today\nSKU,Total Qty.\nDD-1,10\n"
        ).json()

        assert body["header_row_number"] == 3
        assert body["items_created"] == 1

    def test_a_cp1252_export_decodes(self, signed_in: TestClient) -> None:
        response = upload(
            signed_in, "stock.csv", "SKU,Product Name,Total Qty.\nDD-1,Café,10\n".encode("cp1252")
        )

        assert response.status_code == 200
        assert one_item("dd1").product_name == "Café"

    def test_a_semicolon_delimited_export_is_read(self, signed_in: TestClient) -> None:
        response = csv_upload(signed_in, "SKU;Product Name;Total Qty.\nDD-1;Fan;10\n")

        assert response.status_code == 200
        assert one_item("dd1").total_qty == 10


class TestExcelImport:
    def test_it_reads_an_xlsx_the_same_way(self, signed_in: TestClient) -> None:
        response = xlsx_upload(
            signed_in,
            [["SKU", "Product Name", "Total Qty."], ["DD-1", "Fan", 10], ["DD-2", "Bag", 20]],
        )

        assert response.status_code == 200
        assert response.json()["items_created"] == 2
        assert [i.total_qty for i in items()] == [10, 20]

    def test_numeric_cells_arrive_as_numbers(self, signed_in: TestClient) -> None:
        xlsx_upload(signed_in, [["SKU", "Total Qty.", "Price"], ["DD-1", 7, 249.5]])

        item = one_item("dd1")
        assert item.total_qty == 7
        assert item.price_paise == 24950

    def test_history_records_the_method_each_doorway_used(self, signed_in: TestClient) -> None:
        xlsx_upload(signed_in, [["SKU", "Total Qty."], ["DD-1", 1]])
        csv_upload(signed_in, "SKU,Total Qty.\nDD-2,1\n")

        assert [b.method for b in batches()] == ["excel_upload", "csv_upload"]


class TestSkuMapping:
    def test_the_sku_is_the_only_required_column(self, signed_in: TestClient) -> None:
        """A sheet carrying fewer columns is still worth importing."""
        response = csv_upload(signed_in, "SKU\nDD-1\nDD-2\n")

        assert response.status_code == 200
        assert response.json()["items_created"] == 2
        assert one_item("dd1").total_qty == 0

    def test_a_sheet_with_no_sku_column_is_refused(self, signed_in: TestClient) -> None:
        response = csv_upload(signed_in, "Product Name,Total Qty.\nFan,10\n")

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "missing_headers"
        assert error["detail"]["missing"] == ["sku"]
        assert error["next"]

    @pytest.mark.parametrize(
        "header", ["SKU", "sku", "SKU Code", "Item Code", "Product Code", "Article Code"]
    )
    def test_the_spellings_a_sheet_actually_uses(self, signed_in: TestClient, header: str) -> None:
        response = csv_upload(signed_in, f"{header},Total Qty.\nDD-1,10\n")

        assert response.status_code == 200
        assert response.json()["items_created"] == 1

    def test_a_row_with_no_sku_is_rejected_and_numbered(self, signed_in: TestClient) -> None:
        body = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n,5\n").json()

        assert body["items_created"] == 1
        assert [r["row_number"] for r in body["rejected"]] == [3]
        assert body["rejected"][0]["reason"] == "missing_sku"

    def test_a_sku_of_punctuation_alone_is_rejected(self, signed_in: TestClient) -> None:
        """It normalises to nothing, so it could never match a Shopify sale.

        Every row being rejected leaves nothing to import, and that is refused
        outright rather than reported as a successful import of zero rows.
        """
        response = csv_upload(signed_in, "SKU,Total Qty.\n###,5\n")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "no_usable_rows"
        assert items() == []

    def test_a_bad_sku_beside_a_good_one_rejects_only_its_row(self, signed_in: TestClient) -> None:
        body = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n###,5\n").json()

        assert body["items_created"] == 1
        assert body["rejected"][0]["reason"] == "missing_sku"

    def test_the_sku_as_written_is_kept_for_display(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\ndd 1001,10\n")

        assert one_item("dd1001").sku == "dd 1001"


class TestQuantityMapping:
    def test_total_qty_fills_the_quantity(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,42\n")

        assert one_item("dd1").total_qty == 42

    def test_a_sheet_whose_only_stock_column_is_quantity_still_fills_total_qty(
        self, signed_in: TestClient
    ) -> None:
        """Otherwise a stocked SKU would report as holding nothing."""
        csv_upload(signed_in, "SKU,Quantity\nDD-1,42\n")

        assert one_item("dd1").total_qty == 42

    def test_the_exact_header_beats_a_nickname(self, signed_in: TestClient) -> None:
        """``Units`` is an alias for Total Qty; ``Total Qty.`` is its real name."""
        csv_upload(signed_in, "SKU,Units,Total Qty.\nDD-1,5,42\n")

        assert one_item("dd1").total_qty == 42

    @pytest.mark.parametrize(
        ("cell", "expected"), [("12", 12), (" 7 ", 7), ("3.0", 3), ('"1,200"', 1200)]
    )
    def test_the_number_formats_an_export_writes(
        self, signed_in: TestClient, cell: str, expected: int
    ) -> None:
        """A grouped thousand has to be quoted in a CSV — unquoted, the comma is
        the delimiter and the cell is two columns."""
        csv_upload(signed_in, f"SKU,Total Qty.\nDD-1,{cell}\n")

        assert one_item("dd1").total_qty == expected

    @pytest.mark.parametrize("cell", ["abc", "3.5", "-2"])
    def test_an_unusable_quantity_rejects_only_that_row(
        self, signed_in: TestClient, cell: str
    ) -> None:
        body = csv_upload(signed_in, f"SKU,Total Qty.\nDD-1,10\nDD-2,{cell}\n").json()

        assert body["items_created"] == 1
        assert body["rejected"][0]["reason"] == "bad_quantity"
        assert body["rejected"][0]["row_number"] == 3


class TestDuplicateAggregation:
    def test_the_same_sku_twice_is_merged_into_one_row(self, signed_in: TestClient) -> None:
        body = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-1,15\n").json()

        assert body["items_created"] == 1
        assert one_item("dd1").total_qty == 25

    def test_it_reports_which_rows_it_merged(self, signed_in: TestClient) -> None:
        body = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-1,15\n").json()

        assert body["duplicates"] == [{"sku": "DD-1", "rows": [2, 3], "merged_quantity": 25}]
        assert body["batch"]["rows_merged"] == 1

    def test_spellings_that_normalise_alike_are_one_sku(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1001,10\ndd 1001,15\nDD_1001,5\n")

        assert len(items()) == 1
        assert one_item("dd1001").total_qty == 30

    def test_the_first_row_supplies_the_descriptive_fields(self, signed_in: TestClient) -> None:
        csv_upload(
            signed_in,
            "SKU,Product Name,Category,Total Qty.\nDD-1,Ceiling Fan,Fans,10\nDD-1,,Other,15\n",
        )

        item = one_item("dd1")
        assert item.product_name == "Ceiling Fan"
        assert item.category == "Fans"

    def test_the_counts_are_summed_too(self, signed_in: TestClient) -> None:
        """Two warehouse rows carry two halves of one tally."""
        csv_upload(
            signed_in,
            "SKU,Total Qty.,Total Count,Total Orders,Missing\nDD-1,10,4,3,2\nDD-1,15,2,1,1\n",
        )

        item = one_item("dd1")
        assert item.total_count == 6
        assert item.total_orders == 4
        assert item.missing == 3


class TestComplaintAggregation:
    """The aggregated format: one row per SKU, complaint counts already totalled."""

    SHEET = "SKU,Total Qty.,Item Defect Partial,Item Damage Complete,Missing\nDD-1,10,2,3,4\n"

    def test_each_category_lands_in_its_own_column(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, self.SHEET)

        item = one_item("dd1")
        assert item.item_defect_partial == 2
        assert item.item_damage_complete == 3
        assert item.missing == 4
        assert complaint_total(item) == 9

    def test_a_blank_complaint_cell_means_none_not_unknown(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-1,10,\n")

        assert one_item("dd1").missing == 0

    def test_counts_are_restated_not_accumulated(self, signed_in: TestClient) -> None:
        """A re-import is the sheet saying what is true now."""
        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-1,10,5\n")
        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-1,10,2\n")

        assert one_item("dd1").missing == 2

    def test_an_aggregated_sheet_carries_no_dates(self, signed_in: TestClient) -> None:
        """The format has no date column, so nothing can be placed in a window."""
        csv_upload(signed_in, self.SHEET)

        assert dated_complaints() == []


class TestTheComplaintFormat:
    """One row per complaint, grouped into one row per SKU here."""

    def sheet(self) -> str:
        day = days_ago(2)
        return (
            "Date,Order No,SKU Code,Reason,Employee\n"
            f"{day},INV-1,DD-1,Damage,anita\n"
            f"{day},INV-1,DD-1,Missing Part,anita\n"
            f"{day},INV-2,DD-1,Non Working,ravi\n"
            f"{day},INV-3,DD-2,Wrong Item,ravi\n"
        )

    def test_it_reports_the_format_it_detected(self, signed_in: TestClient) -> None:
        body = csv_upload(signed_in, self.sheet()).json()

        assert body["sheet_format"] == "complaints"
        assert body["batch"]["rows_read"] == 4
        assert body["items_created"] == 2

    def test_the_count_columns_are_derived_from_the_rows(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, self.sheet())

        item = one_item("dd1")
        assert item.total_count == 3  # one row per complaint
        assert item.total_orders == 2  # INV-1 twice is one order
        assert item.total_qty == 3  # one unit per row, absent a quantity column

    def test_each_reason_reaches_its_category(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, self.sheet())

        item = one_item("dd1")
        assert item.item_damage_complete == 1
        assert item.missing_part == 1
        assert item.electronics_nonworking_complete == 1

    @pytest.mark.parametrize(
        ("reason", "attribute"),
        [
            ("Damage Partial", "item_damage_partial"),
            ("damaged in transit - partial", "item_damage_partial"),
            ("Damage", "item_damage_complete"),
            ("defective - partial", "item_defect_partial"),
            ("Defective", "item_defect_complete"),
            ("non working partial", "electronics_nonworking_partial"),
            ("Not Working", "electronics_nonworking_complete"),
            ("Missing Part", "missing_part"),
            ("Missing", "missing"),
            ("Wrong Item Delivered", "item_mismatch_wrong_item"),
            ("wrong parcel", "order_wrong_parcel"),
        ],
    )
    def test_partial_is_never_filed_as_complete(
        self, signed_in: TestClient, reason: str, attribute: str
    ) -> None:
        csv_upload(signed_in, f"Date,Order No,SKU Code,Reason\n{days_ago(1)},I-1,DD-1,{reason}\n")

        assert getattr(one_item("dd1"), attribute) == 1

    def test_a_reason_it_cannot_place_is_reported_not_swallowed(
        self, signed_in: TestClient
    ) -> None:
        body = csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n"
            f"{days_ago(1)},I-1,DD-1,Missing\n"
            f"{days_ago(1)},I-2,DD-1,Customer changed mind\n",
        ).json()

        assert body["unmapped_reasons"] == {"Customer changed mind": 1}
        # The row still counts towards the totals; only the breakdown misses it.
        assert one_item("dd1").total_count == 2
        assert complaint_total(one_item("dd1")) == 1


class TestComplaintDates:
    """The date on a complaint row is stored, so the rate can be windowed."""

    def test_one_row_per_sku_per_day(self, signed_in: TestClient) -> None:
        csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n"
            "2026-01-05,INV-1,DD-1,Missing\n"
            "2026-01-05,INV-2,DD-1,Damage\n"
            "2026-01-06,INV-3,DD-1,Defective\n"
            "2026-01-06,INV-4,DD-2,Missing Part\n",
        )
        rows = dated_complaints()

        assert [(r.sku_normalized, r.complaint_date) for r in rows] == [
            ("dd1", date(2026, 1, 5)),
            ("dd1", date(2026, 1, 6)),
            ("dd2", date(2026, 1, 6)),
        ]
        assert rows[0].total_complaints == 2
        assert rows[0].missing == 1
        assert rows[0].item_damage_complete == 1

    @pytest.mark.parametrize(
        "written",
        [
            "2026-01-05",
            "05-01-2026",
            "05/01/2026",
            "05.01.2026",
            "5-Jan-2026",
            "2026-01-05 09:31",
        ],
    )
    def test_the_date_spellings_an_export_writes(self, signed_in: TestClient, written: str) -> None:
        csv_upload(signed_in, f"Date,Order No,SKU Code,Reason\n{written},I-1,DD-1,Missing\n")

        assert [r.complaint_date for r in dated_complaints()] == [date(2026, 1, 5)]

    def test_a_stored_row_agrees_with_its_own_breakdown(self, signed_in: TestClient) -> None:
        csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n2026-01-05,I-1,DD-1,Missing\n2026-01-05,I-2,DD-1,Damage\n",
        )
        for row in dated_complaints():
            breakdown = sum(getattr(row, attribute) for attribute, _ in COMPLAINT_COLUMNS)
            assert row.total_complaints == breakdown

    def test_the_dated_rows_sum_to_the_sheet_tally(self, signed_in: TestClient) -> None:
        """The two records of the same complaints must agree."""
        csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n"
            "2026-01-05,I-1,DD-1,Missing\n"
            "2026-02-09,I-2,DD-1,Damage\n"
            "2026-03-01,I-3,DD-1,Defective\n",
        )

        assert sum(r.total_complaints for r in dated_complaints()) == 3
        assert complaint_total(one_item("dd1")) == 3

    def test_an_unreadable_date_is_kept_as_undated_not_dropped(self, signed_in: TestClient) -> None:
        csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n"
            "2026-01-05,I-1,DD-1,Missing\n"
            ",I-2,DD-1,Damage\n"
            "rubbish,I-3,DD-1,Defective\n",
        )

        assert sum(r.total_complaints for r in dated_complaints()) == 1
        # All three still reach the sheet's own tally.
        assert complaint_total(one_item("dd1")) == 3

    def test_re_importing_replaces_rather_than_doubling(self, signed_in: TestClient) -> None:
        body = "Date,Order No,SKU Code,Reason\n2026-01-05,I-1,DD-1,Missing\n"
        csv_upload(signed_in, body)
        csv_upload(signed_in, body)

        rows = dated_complaints()
        assert len(rows) == 1
        assert rows[0].total_complaints == 1

    def test_a_restatement_drops_a_day_the_new_sheet_omits(self, signed_in: TestClient) -> None:
        csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n2026-01-05,I-1,DD-1,Missing\n2026-01-06,I-2,DD-1,Damage\n",
        )
        csv_upload(signed_in, "Date,Order No,SKU Code,Reason\n2026-01-05,I-1,DD-1,Missing\n")

        assert [r.complaint_date for r in dated_complaints()] == [date(2026, 1, 5)]

    def test_a_stock_sheet_leaves_the_dated_rows_alone(self, signed_in: TestClient) -> None:
        """A stock sheet says nothing about complaint dates, so it deletes none.

        It used to clear the table, on the reasoning that the same complaints
        would otherwise count twice — dated and undated. The per-SKU rule in
        ``repositories.complaints`` already prevents that: a SKU with dated rows
        never has its aggregated columns read. Clearing was therefore not
        preventing a double count, it was deleting the other file's data.
        """
        csv_upload(signed_in, "Date,Order No,SKU Code,Reason\n2026-01-05,I-1,DD-1,Missing\n")
        assert len(dated_complaints()) == 1

        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-1,10,4\n")

        assert [r.complaint_date for r in dated_complaints()] == [date(2026, 1, 5)]

    def test_a_complaint_export_still_replaces_every_dated_row(self, signed_in: TestClient) -> None:
        """It is one file stating the whole dated record, so it owns that table."""
        csv_upload(signed_in, "Date,Order No,SKU Code,Reason\n2026-01-05,I-1,DD-1,Missing\n")
        csv_upload(signed_in, "Date,Order No,SKU Code,Reason\n2026-02-05,I-2,DD-2,Damage\n")

        assert {r.sku_normalized for r in dated_complaints()} == {"dd2"}

    def test_a_complaint_export_does_not_remove_the_stock_sheets_skus(
        self, signed_in: TestClient
    ) -> None:
        """The fix for the alternating-import data loss.

        On the live workspace a stock sheet and a complaint export alternated for
        24 imports and each erased the other, which is why eleven successful
        complaint imports left no dated rows at all. A complaint export knows
        nothing about the catalogue and may not shrink it.
        """
        csv_upload(signed_in, "SKU,Total Qty.,Total Orders.\nDD-1,10,3\nDD-2,20,4\n")
        assert {i.sku_normalized for i in items()} == {"dd1", "dd2"}

        csv_upload(signed_in, "Date,Order No,SKU Code,Reason\n2026-02-05,I-2,DD-1,Damage\n")

        held = {i.sku_normalized: i for i in items()}
        assert set(held) == {"dd1", "dd2"}, "the complaint export must not remove DD-2"
        # DD-2's stock survived untouched; DD-1 kept its stock and gained the complaint.
        assert held["dd2"].total_qty == 20
        assert held["dd1"].total_qty == 10
        assert held["dd1"].item_damage_complete == 1


class TestComplaintsFollowTheFormatTheyArrivedIn:
    """Both upload formats are supported, and they answer the range differently.

    A complaint export carries a date per row, so its complaints can be — and
    are — filtered to the selected window. An aggregated sheet carries no date
    column at all, so its totals stand in every range and the page says so.
    """

    def complaints_on(self, client: TestClient, *, days_back: int) -> None:
        csv_upload(
            client,
            "Date,Order No,SKU Code,Reason\n"
            f"{days_ago(days_back)},I-1,DD-1,Missing\n"
            f"{days_ago(days_back)},I-2,DD-1,Damage\n",
        )

    def test_a_dated_export_is_filtered_out_of_a_narrower_window(
        self, signed_in: TestClient
    ) -> None:
        """Complaints from 45 days back are outside a 30-day view."""
        self.complaints_on(signed_in, days_back=45)

        narrow = signed_in.get(f"{INSIGHTS}?days=30").json()["kpis"]
        wide = signed_in.get(f"{INSIGHTS}?days=90").json()["kpis"]

        assert narrow["total_complaints"] == 0
        assert wide["total_complaints"] == 2

    def test_a_custom_range_selects_them_too(self, signed_in: TestClient) -> None:
        self.complaints_on(signed_in, days_back=10)
        today = date.today()

        covering = signed_in.get(
            f"{PERFORMANCE}?since={today - timedelta(days=12)}&until={today}&limit=10"
        ).json()
        before = signed_in.get(
            f"{PERFORMANCE}?since={today - timedelta(days=60)}"
            f"&until={today - timedelta(days=30)}&limit=10"
        ).json()

        assert sum(r["total_complaints"] for r in covering["rows"]) == 2
        assert sum(r["total_complaints"] for r in before["rows"]) == 0

    def test_a_dated_export_needs_no_note(self, signed_in: TestClient) -> None:
        self.complaints_on(signed_in, days_back=2)

        scope = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["complaint_scope"]

        assert scope["filtered_by_date"] is True
        assert scope["undated_complaints"] == 0
        assert scope["undated_skus"] == 0

    def test_an_aggregated_sheet_reports_its_complaints_in_full(
        self, signed_in: TestClient
    ) -> None:
        """The format carries no dates, so its totals stand in every range."""
        csv_upload(signed_in, "SKU,Total Qty.,Missing,Item Damage Complete\nDD-1,10,3,4\n")

        for query in ("days=30", "days=365"):
            row = signed_in.get(f"{PERFORMANCE}?{query}&limit=10").json()["rows"][0]

            assert row["total_complaints"] == 7, query
            assert row["complaints"]["missing"] == 3, query
            assert row["complaints"]["item_damage_complete"] == 4, query

    def test_an_aggregated_sheet_says_why_it_is_not_filtered(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.,Missing,Item Damage Complete\nDD-1,10,3,4\n")

        scope = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["complaint_scope"]

        assert scope["filtered_by_date"] is False
        assert scope["undated_skus"] == 1
        assert scope["undated_complaints"] == 7

    def test_a_mixed_file_filters_what_it_can_and_says_so(self, signed_in: TestClient) -> None:
        """One file, some rows dated and some not — the only way a workspace is
        mixed now that an import replaces the whole dataset.

        DD-1's complaint carries a day and answers the range. DD-2's does not,
        so it has no dated record at all and its total stands in every window,
        which is what the note is for.
        """
        csv_upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n"
            f"{days_ago(2)},I-1,DD-1,Missing\n"
            ",I-2,DD-2,Damage\n",  # no date on this row
        )

        wide = {
            r["sku"]: r["total_complaints"]
            for r in signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["rows"]
        }
        narrow = signed_in.get(f"{PERFORMANCE}?days=1&limit=10").json()
        by_sku = {r["sku"]: r["total_complaints"] for r in narrow["rows"]}

        assert wide == {"DD-1": 1, "DD-2": 1}
        # A one-day window drops the dated SKU's complaint and keeps the other's.
        assert by_sku["DD-1"] == 0
        assert by_sku["DD-2"] == 1

        scope = narrow["complaint_scope"]
        assert scope["dated_skus"] == 1
        assert scope["undated_skus"] == 1
        assert scope["undated_complaints"] == 1

    def test_a_dated_export_reports_the_same_way(self, signed_in: TestClient) -> None:
        self.complaints_on(signed_in, days_back=2)

        row = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["rows"][0]

        assert row["total_complaints"] == 2

    def test_the_count_needs_no_shopify_sales_at_all(self, signed_in: TestClient) -> None:
        """It is a sheet column, so an unsynced store still reports it."""
        self.complaints_on(signed_in, days_back=2)

        row = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["rows"][0]

        assert row["total_complaints"] == 2
        assert row["total_qty"] == 2  # one unit per complaint row
        assert row["shopify_sales"] == 0
        assert row["shopify_sales_pct"] == 0.0

    def test_no_row_carries_a_complaint_rate(self, signed_in: TestClient) -> None:
        """The metric was removed from the project — field and column."""
        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-1,0,4\n")

        body = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()

        assert body["rows"][0]["total_complaints"] == 4
        assert "complaint_rate" not in body["rows"][0]

    def test_it_cannot_be_sorted_by(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-1,10,4\n")

        assert signed_in.get(f"{PERFORMANCE}?sort=complaint_rate").status_code == 422

    def test_both_import_formats_are_treated_alike(self, signed_in: TestClient) -> None:
        """Either format can be the whole dataset; whichever came last is it."""
        csv_upload(signed_in, f"Date,Order No,SKU Code,Reason\n{days_ago(3)},I-1,DD-1,Missing\n")
        first = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["rows"]
        assert {r["sku"]: r["total_complaints"] for r in first} == {"DD-1": 1}

        csv_upload(signed_in, "SKU,Total Qty.,Missing\nDD-2,10,6\n")

        second = signed_in.get(f"{PERFORMANCE}?days=30&limit=10").json()["rows"]
        assert {r["sku"]: r["total_complaints"] for r in second} == {"DD-2": 6}


class TestImportDrivenAnalytics:
    """An import is finished when its SKUs are matched, not when the rows land."""

    def test_the_response_carries_the_analysis(self, signed_in: TestClient) -> None:
        analysis = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-2,15\n").json()["analysis"]

        assert analysis["skus_analyzed"] == 2
        assert analysis["skus_matched"] + analysis["skus_unmatched"] == 2

    def test_a_sku_with_no_shopify_sale_counts_as_unmatched(self, signed_in: TestClient) -> None:
        analysis = csv_upload(signed_in, "SKU,Total Qty.\nNOPE-1,10\n").json()["analysis"]

        assert analysis["skus_unmatched"] == 1
        assert analysis["shopify_sales"] == 0
        assert analysis["shopify_sales_pct"] == 0.0

    def test_the_summary_counts_complaints_and_offers_no_rate(self, signed_in: TestClient) -> None:
        analysis = csv_upload(
            signed_in, f"Date,Order No,SKU Code,Reason\n{days_ago(1)},I-1,DD-1,Missing\n"
        ).json()["analysis"]

        assert analysis["total_complaints"] == 1
        assert analysis["shopify_sales"] == 0
        assert "complaint_rate" not in analysis

    def test_the_analysis_matches_what_the_dashboard_then_shows(
        self, signed_in: TestClient
    ) -> None:
        """The import and the dashboard must not disagree the moment it lands."""
        analysis = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-2,15\n").json()["analysis"]
        kpis = signed_in.get("/api/analytics/kpis", params={"days": 30}).json()

        assert analysis["skus_analyzed"] == kpis["total_skus"]
        assert analysis["shopify_sales"] == kpis["shopify_sales"]

    def test_it_never_costs_a_shopify_request(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The analysis reads the rollup, not the store."""

        def explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("the import must not reach the network")

        monkeypatch.setattr(import_url.httpx, "Client", explode)

        assert csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n").status_code == 200


class TestBackgroundJobs:
    """The rollup refresh is queued after the commit, on the shared worker."""

    def test_one_job_is_submitted_per_import(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.workers import runner

        names: list[str] = []
        real = runner.submit

        def record(job: Callable[[], None], *, name: str):
            names.append(name)
            return real(job, name=name)

        monkeypatch.setattr("app.api.routes.imports.runner.submit", record)
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n")

        assert len(names) == 1
        assert names[0].startswith("rollup-after-import-")

    def test_a_failing_job_does_not_fail_the_upload(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rows are already committed; the user must not see a 500."""

        def explode(_workspace_id: int) -> None:
            raise RuntimeError("rollup exploded")

        monkeypatch.setattr("app.services.imports.refresh_rollup_job", explode)

        assert csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n").status_code == 200
        assert len(items()) == 1


class TestErrorHandling:
    def test_an_empty_file_is_refused(self, signed_in: TestClient) -> None:
        response = csv_upload(signed_in, "")

        assert response.status_code == 422
        assert response.json()["error"]["code"] in {"empty_file", "missing_headers"}

    def test_a_header_with_no_rows_under_it_is_refused(self, signed_in: TestClient) -> None:
        response = csv_upload(signed_in, "SKU,Total Qty.\n")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "empty_file"

    def test_an_unsupported_file_type_is_refused(self, signed_in: TestClient) -> None:
        response = upload(signed_in, "notes.txt", b"hello", "text/plain")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_file_type"

    def test_a_file_that_is_not_a_workbook_is_refused(self, signed_in: TestClient) -> None:
        response = upload(
            signed_in,
            "stock.xlsx",
            b"not a workbook",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "import_unreadable"

    def test_every_error_carries_a_code_and_a_next_step(self, signed_in: TestClient) -> None:
        error = csv_upload(signed_in, "Product Name\nFan\n").json()["error"]

        assert error["code"]
        assert error["message"]
        assert error["next"]


class TestTransactionBoundaries:
    """A refused import writes its batch and nothing else."""

    def test_a_refused_file_stores_no_inventory(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "Product Name,Total Qty.\nFan,10\n")

        assert items() == []

    def test_a_refused_file_still_records_the_attempt(self, signed_in: TestClient) -> None:
        """The batch is committed on purpose: History has to show what was tried."""
        csv_upload(signed_in, "Product Name,Total Qty.\nFan,10\n")

        batch = batches()[-1]
        assert batch.status == "failed"
        assert batch.error_code == "missing_headers"
        assert batch.finished_at is not None

    def test_a_partial_file_commits_the_rows_that_parsed(self, signed_in: TestClient) -> None:
        body = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-2,nonsense\n").json()

        assert body["batch"]["status"] == "partial"
        assert body["batch"]["rows_rejected"] == 1
        assert [i.sku for i in items()] == ["DD-1"]

    def test_a_refused_import_leaves_earlier_data_intact(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\n")
        csv_upload(signed_in, "Product Name,Total Qty.\nFan,10\n")

        assert one_item("dd1").total_qty == 10

    def test_a_refused_import_leaves_no_dated_complaints(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "Date,Order No,Reason\n2026-01-05,I-1,Missing\n")

        assert dated_complaints() == []


class TestImportHistory:
    def test_it_lists_newest_first(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,1\n")
        csv_upload(signed_in, "SKU,Total Qty.\nDD-2,2\n")

        body = signed_in.get(HISTORY).json()

        assert body["total"] == 2
        assert body["items"][0]["id"] > body["items"][1]["id"]

    def test_a_row_carries_what_the_import_did(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-1,5\nDD-2,bad\n")

        row = signed_in.get(HISTORY).json()["items"][0]

        assert row["rows_read"] == 3
        assert row["rows_imported"] == 1
        assert row["rows_merged"] == 1
        assert row["rows_rejected"] == 1
        assert row["origin_filename"] == "stock.csv"
        assert row["duration_ms"] is not None

    def test_a_failure_carries_its_message(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "Product Name\nFan\n")

        row = signed_in.get(HISTORY).json()["items"][0]

        assert row["status"] == "failed"
        assert row["error_code"] == "missing_headers"
        assert row["error_detail"]

    def test_the_status_filter_separates_them(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,1\n")
        csv_upload(signed_in, "Product Name\nFan\n")

        failed = signed_in.get(HISTORY, params={"status": "failed"}).json()
        complete = signed_in.get(HISTORY, params={"status": "complete"}).json()

        assert failed["total"] == 1
        assert failed["items"][0]["status"] == "failed"
        assert complete["total"] == 1
        assert complete["items"][0]["status"] == "complete"

    def test_an_unknown_status_is_refused(self, signed_in: TestClient) -> None:
        assert signed_in.get(HISTORY, params={"status": "sideways"}).status_code == 422

    def test_it_pages(self, signed_in: TestClient) -> None:
        for n in range(3):
            csv_upload(signed_in, f"SKU,Total Qty.\nDD-{n},1\n")

        page = signed_in.get(HISTORY, params={"limit": 2, "offset": 2}).json()

        assert page["total"] == 3
        assert len(page["items"]) == 1
        assert page["limit"] == 2
        assert page["offset"] == 2

    def test_one_import_can_be_fetched_by_id(self, signed_in: TestClient) -> None:
        created = csv_upload(signed_in, "SKU,Total Qty.\nDD-1,1\n").json()["batch"]

        body = signed_in.get(f"{HISTORY}/{created['id']}").json()

        assert body["id"] == created["id"]
        assert body["rows_imported"] == 1

    def test_an_unknown_id_is_a_404(self, signed_in: TestClient) -> None:
        response = signed_in.get(f"{HISTORY}/9999")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "import_not_found"

    def test_the_sheets_route_wins_over_the_id_route(self, signed_in: TestClient) -> None:
        """``/imports/sheets`` and ``/imports/{id}`` share a prefix."""
        assert signed_in.get(SHEETS).status_code == 200


class TestInventoryEndpoints:
    def test_the_summary_counts_what_was_imported(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-2,15\n")

        body = signed_in.get(SUMMARY).json()

        assert body["total_skus"] == 2
        assert body["total_quantity"] == 25
        assert body["last_imported_at"] is not None

    def test_an_empty_workspace_reports_zero_not_null(self, signed_in: TestClient) -> None:
        body = signed_in.get(SUMMARY).json()

        assert body["total_skus"] == 0
        assert body["total_quantity"] == 0
        assert body["last_imported_at"] is None

    def test_the_list_pages(self, signed_in: TestClient) -> None:
        csv_upload(signed_in, "SKU,Total Qty.\nDD-1,10\nDD-2,15\nDD-3,20\n")

        page = signed_in.get(INVENTORY, params={"limit": 2, "offset": 0}).json()

        assert page["total"] == 3
        assert len(page["items"]) == 2


class TestGoogleSheetImport:
    """The same import, reached by exporting a sheet as CSV."""

    def test_it_imports_the_rows(self, signed_in: TestClient, sheet_csv: list[str]) -> None:
        response = signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert response.status_code == 200
        assert response.json()["items_created"] == 2
        assert [i.sku for i in items()] == ["DD-1", "DD-2"]

    def test_it_asks_google_for_a_csv_export(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert sheet_csv
        assert "format=csv" in sheet_csv[0]
        assert "1AbCdEfGhIjKlMnOp" in sheet_csv[0]

    def test_history_records_it_as_a_google_sheet(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        batch = batches()[-1]
        assert batch.method == "google_sheet"
        assert batch.origin_filename.startswith("google-sheet-")

    def test_a_link_that_is_not_a_sheet_is_refused_without_a_fetch(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = serve(monkeypatch, lambda _r: httpx.Response(200, content=b"x"))

        response = signed_in.post(GOOGLE_SHEET, json={"url": "https://example.com/thing"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "not_a_google_sheet_url"
        assert seen == []

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_private_sheet_says_how_to_share_it(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        serve(monkeypatch, lambda _r: httpx.Response(status))

        response = signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "sheet_not_public"
        assert "Anyone with the link" in error["next"]

    def test_a_sign_in_page_is_the_same_problem(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Google answers a private sheet with HTML, not an error status."""
        serve(
            monkeypatch,
            lambda _r: httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html>Sign in</html>"
            ),
        )

        response = signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "sheet_not_public"

    def test_a_sheet_missing_a_sku_column_gets_the_ordinary_message(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One parser, so one set of validation messages."""
        serving_csv(monkeypatch, "Product Name,Total Qty.\nFan,10\n")

        response = signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "missing_headers"

    def test_a_failed_sheet_import_stores_no_inventory(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serving_csv(monkeypatch, "Product Name,Total Qty.\nFan,10\n")

        signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert items() == []


class TestLinkedSheets:
    """Settings → Google Sheets: the sheets a workspace imports from again."""

    def link(self, client: TestClient, *, name: str | None = None):
        body: dict[str, object] = {"url": SHEET_URL}
        if name is not None:
            body["name"] = name
        return client.post(SHEETS, json=body)

    def test_linking_imports_in_the_same_step(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        """A link nothing has ever read is not proof the link works."""
        response = self.link(signed_in, name="Complaints")

        assert response.status_code == 200
        assert response.json()["items_created"] == 2
        assert [i.sku for i in items()] == ["DD-1", "DD-2"]

    def test_the_link_is_recorded_with_its_outcome(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        result = self.link(signed_in, name="Complaints").json()

        sheet = linked_sheets()[0]
        assert sheet.name == "Complaints"
        assert sheet.url == SHEET_URL
        assert sheet.last_status == "complete"
        assert sheet.last_batch_id == result["batch"]["id"]
        assert sheet.last_synced_at is not None

    def test_an_unreadable_sheet_is_never_linked(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve(monkeypatch, lambda _r: httpx.Response(403))

        response = self.link(signed_in)

        assert response.status_code == 422
        assert linked_sheets() == []

    def test_it_lists_what_is_linked(self, signed_in: TestClient, sheet_csv: list[str]) -> None:
        self.link(signed_in, name="Complaints")

        body = signed_in.get(SHEETS).json()

        assert len(body["items"]) == 1
        assert body["items"][0]["name"] == "Complaints"
        assert body["items"][0]["last_status"] == "complete"

    def test_the_import_page_links_the_sheet_too(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        """Otherwise the two doorways disagree about what is linked."""
        signed_in.post(GOOGLE_SHEET, json={"url": SHEET_URL})

        assert len(linked_sheets()) == 1

    def test_the_same_sheet_twice_is_one_link(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        self.link(signed_in, name="Complaints")
        self.link(signed_in, name="Complaints")

        assert len(linked_sheets()) == 1

    def test_a_resync_runs_the_import_again(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        self.link(signed_in)
        sheet_id = linked_sheets()[0].id

        response = signed_in.post(f"{SHEETS}/{sheet_id}/resync")

        assert response.status_code == 200
        assert len(batches()) == 2
        assert linked_sheets()[0].last_batch_id == response.json()["batch"]["id"]

    def test_a_failed_resync_says_so_rather_than_keeping_the_old_success(
        self, signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Showing the previous success hides the problem Settings exists to surface."""
        serving_csv(monkeypatch, "SKU,Total Qty.\nDD-1,10\n")
        self.link(signed_in)
        sheet_id = linked_sheets()[0].id
        assert linked_sheets()[0].last_status == "complete"

        serve(monkeypatch, lambda _r: httpx.Response(403))
        response = signed_in.post(f"{SHEETS}/{sheet_id}/resync")

        assert response.status_code == 422
        assert linked_sheets()[0].last_status == "failed"

    def test_unlinking_forgets_the_address_only(
        self, signed_in: TestClient, sheet_csv: list[str]
    ) -> None:
        """It stops offering to re-run the import; it does not undo one."""
        self.link(signed_in)
        sheet_id = linked_sheets()[0].id

        response = signed_in.delete(f"{SHEETS}/{sheet_id}")

        assert response.status_code == 204
        assert linked_sheets() == []
        assert len(items()) == 2
        assert len(batches()) == 1

    def test_resyncing_an_unknown_sheet_is_a_404(self, signed_in: TestClient) -> None:
        response = signed_in.post(f"{SHEETS}/9999/resync")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "sheet_not_found"

    def test_unlinking_an_unknown_sheet_is_a_404(self, signed_in: TestClient) -> None:
        assert signed_in.delete(f"{SHEETS}/9999").status_code == 404
