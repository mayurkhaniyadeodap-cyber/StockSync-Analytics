"""Parsing rules, tested against real file bytes rather than a mock reader."""

from __future__ import annotations

import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from app.models import COMPLAINT_COLUMNS
from app.services import import_files
from app.services.import_files import (
    EmptyFileError,
    MissingHeadersError,
    UnsupportedFileTypeError,
    parse_inventory_file,
    parse_price_paise,
    parse_quantity,
)


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestQuantityParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12", 12),
            ("  12 ", 12),
            ("1,240", 1240),
            ("12.0", 12),  # spreadsheets hand back floats for integer columns
            (12.0, 12),
            (0, 0),
        ],
    )
    def test_accepts(self, raw: object, expected: int) -> None:
        assert parse_quantity(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "abc", "12.5", "1/2"])
    def test_rejects(self, raw: object) -> None:
        assert parse_quantity(raw) is None

    def test_fractional_is_rejected_not_rounded(self) -> None:
        """Rounding 2.5 to 2 or 3 would be a stock error nobody would catch."""
        assert parse_quantity("2.5") is None


class TestPriceParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("199", 19900),
            ("199.50", 19950),
            ("₹1,299.00", 129900),
            ("Rs. 45", 4500),
            (12.99, 1299),  # the float-multiplication trap: int(12.99*100) == 1298
        ],
    )
    def test_converts_to_paise(self, raw: object, expected: int) -> None:
        assert parse_price_paise(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "n/a", "-"])
    def test_absent_is_none(self, raw: object) -> None:
        assert parse_price_paise(raw) is None


class TestCsv:
    def test_reads_a_plain_file(self) -> None:
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes("SKU,Product Name,Quantity,Price\nDD-1001,Steel Bottle,24,199\n"),
        )

        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.sku == "DD-1001"
        assert row.sku_normalized == "dd1001"
        assert row.product_name == "Steel Bottle"
        assert row.quantity == 24
        assert row.price_paise == 19900
        assert not result.rejected

    def test_header_aliases_are_accepted(self) -> None:
        """The SDD's sheet says "Item Code" and "Stock Count", not SKU/Quantity."""
        result = parse_inventory_file(
            "stock.csv", csv_bytes("Item Code,Item Name,Stock Count\nDD-1,Widget,5\n")
        )

        assert result.rows[0].sku == "DD-1"
        assert result.rows[0].quantity == 5
        assert result.detected_columns["sku"] == "Item Code"

    @pytest.mark.parametrize(
        "header",
        [
            "Quantity",
            "Qty",
            "Stock",
            "Stock Qty",
            "Stock Quantity",
            "Available Qty",
            "Available",
            "Inventory",
            "Inventory Qty",
            "On Hand",
        ],
    )
    def test_every_accepted_quantity_header(self, header: str) -> None:
        """The spellings real stock sheets use for the same column."""
        result = parse_inventory_file(
            "stock.csv", csv_bytes(f"SKU,Product Name,{header}\nDD-1,Widget,5\n")
        )

        assert not result.rejected
        assert result.rows[0].quantity == 5
        assert result.detected_columns["quantity"] == header

    @pytest.mark.parametrize(
        "header", ["quantity", "QUANTITY", "Qty", "  qty  ", "On-Hand", "stock_qty"]
    )
    def test_quantity_header_ignores_case_and_punctuation(self, header: str) -> None:
        """Case, spaces, dots, hyphens and underscores all fold away."""
        result = parse_inventory_file("stock.csv", csv_bytes(f"SKU,{header}\nDD-1,5\n"))

        assert result.rows[0].quantity == 5

    @pytest.mark.parametrize(
        "header", ["Total Qty.", "total qty.", "TOTAL-QTY", "Total_Qty", "ToTaL.QtY."]
    )
    def test_total_qty_is_the_quantity(self, header: str) -> None:
        """The real export has no Quantity column: Total Qty. is the stock figure."""
        result = parse_inventory_file("stock.csv", csv_bytes(f"SKU,{header}\nDD-1,99\n"))

        row = result.rows[0]
        assert row.quantity == 99
        assert row.counts["total_qty"] == 99  # and it keeps its own column too

    def test_total_qty_wins_over_a_quantity_column(self) -> None:
        """A sheet carrying both is read as the export means it.

        Total Qty. is the figure the business reconciles against, so when the two
        disagree it is the one that counts — not whichever came first.
        """
        result = parse_inventory_file(
            "stock.csv", csv_bytes("SKU,Quantity,Total Qty.\nDD-1,5,99\n")
        )

        assert result.rows[0].quantity == 99

    def test_a_quantity_column_alone_still_works(self) -> None:
        """Older sheets predating the fixed format are not refused."""
        result = parse_inventory_file("stock.csv", csv_bytes("SKU,Quantity\nDD-1,5\n"))

        assert result.rows[0].quantity == 5

    def test_semicolon_delimiter(self) -> None:
        """Excel writes semicolons under a European locale."""
        result = parse_inventory_file("stock.csv", csv_bytes("SKU;Quantity;Price\nDD-2;7;10,50\n"))

        assert result.rows[0].sku == "DD-2"
        assert result.rows[0].quantity == 7

    def test_windows_1252_encoding(self) -> None:
        """Q4: ERP exports are often cp1252, which is not valid UTF-8."""
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes("SKU,Product Name,Quantity\nDD-3,Café Mug,3\n", encoding="cp1252"),
        )

        assert result.rows[0].product_name == "Café Mug"

    def test_utf8_bom_does_not_corrupt_the_first_header(self) -> None:
        result = parse_inventory_file("stock.csv", "SKU,Quantity\nDD-4,2\n".encode("utf-8-sig"))

        assert result.rows[0].sku == "DD-4"

    def test_title_row_above_the_headers_is_skipped(self) -> None:
        """The plan names this as the exact shape that broke a sample file."""
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes(
                "Warehouse Ahmedabad — July export\n\nSKU,Product Name,Quantity\nDD-5,Rope,9\n"
            ),
        )

        assert result.header_row_number == 2  # blank rows dropped before scanning
        assert result.rows[0].sku == "DD-5"

    def test_a_sheet_with_no_sku_is_the_one_rejection_left(self) -> None:
        """The SKU is the join key: there is nothing to infer it from."""
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file("stock.csv", csv_bytes("Product Name,Total Qty.\nThing,4\n"))

        assert caught.value.detail["missing"] == ["sku"]
        assert caught.value.detail["expected_columns"] == ["SKU"]
        assert "Total Qty." in caught.value.detail["found_columns"]

    def test_a_sheet_with_only_a_sku_column_imports(self) -> None:
        """Everything but the SKU is optional, so this is a valid import."""
        result = parse_inventory_file("stock.csv", csv_bytes("SKU\nDD-6\n"))

        assert [row.sku for row in result.rows] == ["DD-6"]
        assert result.rows[0].quantity == 0
        assert not result.rejected

    def test_empty_file_is_rejected(self) -> None:
        with pytest.raises(EmptyFileError):
            parse_inventory_file("stock.csv", b"")

    def test_headers_only_is_rejected(self) -> None:
        with pytest.raises(EmptyFileError):
            parse_inventory_file("stock.csv", csv_bytes("SKU,Quantity\n"))


class TestRowRejection:
    def test_row_without_a_sku_is_rejected_not_imported(self) -> None:
        result = parse_inventory_file("stock.csv", csv_bytes("SKU,Quantity\nDD-1,5\n,7\nDD-2,3\n"))

        assert [r.sku for r in result.rows] == ["DD-1", "DD-2"]
        assert len(result.rejected) == 1
        assert result.rejected[0].reason == "missing_sku"
        assert result.rejected[0].row_number == 3

    def test_unparseable_quantity_is_rejected(self) -> None:
        result = parse_inventory_file("stock.csv", csv_bytes("SKU,Quantity\nDD-1,lots\n"))

        assert not result.rows
        assert result.rejected[0].reason == "bad_quantity"

    def test_negative_quantity_is_rejected(self) -> None:
        result = parse_inventory_file("stock.csv", csv_bytes("SKU,Quantity\nDD-1,-4\n"))

        assert result.rejected[0].reason == "bad_quantity"

    def test_punctuation_only_sku_is_rejected(self) -> None:
        """It normalises to an empty string, so it could never match anything."""
        result = parse_inventory_file("stock.csv", csv_bytes("SKU,Quantity\n---,4\n"))

        assert result.rejected[0].reason == "missing_sku"


class TestDuplicateDetection:
    def test_duplicate_skus_merge_by_summing_quantity(self) -> None:
        """Design doc §8.6 — the same SKU in two warehouse rows is one stock figure."""
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes("SKU,Product Name,Quantity\nDD-1,Bottle,10\nDD-1,Bottle,15\n"),
        )

        assert len(result.rows) == 1
        assert result.rows[0].quantity == 25
        assert len(result.duplicates) == 1
        assert result.duplicates[0].rows == [2, 3]
        assert result.duplicates[0].merged_quantity == 25
        assert result.rows_merged == 1

    def test_duplicates_are_detected_across_spellings(self) -> None:
        """ "DD-1001" and "dd 1001" are the same SKU; normalisation is the key."""
        result = parse_inventory_file(
            "stock.csv", csv_bytes("SKU,Quantity\nDD-1001,4\ndd 1001,6\n")
        )

        assert len(result.rows) == 1
        assert result.rows[0].quantity == 10
        # The first spelling wins for display — it is the one in the sheet.
        assert result.rows[0].sku == "DD-1001"

    def test_three_occurrences_count_two_merges(self) -> None:
        result = parse_inventory_file(
            "stock.csv", csv_bytes("SKU,Quantity\nDD-1,1\nDD-1,2\nDD-1,3\n")
        )

        assert result.rows[0].quantity == 6
        assert result.rows_merged == 2

    def test_first_non_empty_description_survives_the_merge(self) -> None:
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes("SKU,Product Name,Quantity,Price\nDD-1,,5,\nDD-1,Bottle,5,199\n"),
        )

        assert result.rows[0].product_name == "Bottle"
        assert result.rows[0].price_paise == 19900


class TestXlsx:
    def test_reads_an_xlsx(self) -> None:
        raw = xlsx_bytes(
            [["SKU", "Product Name", "Quantity", "Price"], ["DD-9", "Clamp", 14, 249.5]]
        )

        result = parse_inventory_file("stock.xlsx", raw)

        assert result.rows[0].sku == "DD-9"
        assert result.rows[0].quantity == 14
        assert result.rows[0].price_paise == 24950

    def test_title_row_above_headers_in_xlsx(self) -> None:
        raw = xlsx_bytes([["Inventory as at 28 July"], ["SKU", "Quantity"], ["DD-10", 3]])

        result = parse_inventory_file("stock.xlsx", raw)

        assert result.rows[0].sku == "DD-10"

    def test_blank_trailing_rows_are_ignored(self) -> None:
        """Excel readily reports thousands of empty rows below the data."""
        raw = xlsx_bytes([["SKU", "Quantity"], ["DD-11", 2], [None, None], ["", ""]])

        result = parse_inventory_file("stock.xlsx", raw)

        assert len(result.rows) == 1
        assert not result.rejected


#: The exact sheet the business uploads, header for header — trailing dots and
#: lower-case "count" included.
REAL_EXPORT_HEADERS: list[object] = [
    "SKU",
    "Total count.",
    "Total Orders.",
    "Total Qty.",
    "Item Defect Partial",
    "Item Defect Complete",
    "Item Damage Partial",
    "Item Damage Complete",
    "Order Wrong Parcel",
    "Electronics Item Nonworking Partial",
    "Electronics Item Nonworking Complete",
    "Missing",
    "Missing Part",
    "Item Mismatch Wrong Item Delivered",
]


class TestTheRealExport:
    """Written from a file the importer refused, and pinned so it cannot again."""

    def test_it_imports_with_no_quantity_column(self) -> None:
        raw = xlsx_bytes(
            [REAL_EXPORT_HEADERS, ["DD-1", 120, 80, 95, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]
        )

        result = parse_inventory_file("stock.xlsx", raw)

        assert not result.rejected
        assert len(result.detected_columns) == len(REAL_EXPORT_HEADERS)
        row = result.rows[0]
        assert row.quantity == 95  # from Total Qty.
        assert row.counts["total_count"] == 120
        assert row.counts["total_orders"] == 80
        assert sum(row.counts[f] for f in ("item_defect_partial", "item_defect_complete")) == 3

    def test_the_trailing_dots_are_not_the_problem(self) -> None:
        """ "Total count." and "Total Orders." fold to the same keys as the plain names."""
        raw = xlsx_bytes([REAL_EXPORT_HEADERS, ["DD-1", 1, 2, 3] + [0] * 10])

        detected = parse_inventory_file("stock.xlsx", raw).detected_columns

        assert detected["total_count"] == "Total count."
        assert detected["total_orders"] == "Total Orders."
        assert detected["total_qty"] == "Total Qty."


class TestFileType:
    def test_unknown_extension_is_rejected(self) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            parse_inventory_file("stock.pdf", b"%PDF-1.4")

    def test_legacy_xls_explains_how_to_convert(self) -> None:
        """openpyxl cannot read the old binary format; say so usefully."""
        with pytest.raises(UnsupportedFileTypeError) as caught:
            parse_inventory_file("stock.xls", b"\xd0\xcf\x11\xe0")

        assert "Save As" in caught.value.next_step

    def test_corrupt_xlsx_is_a_clean_error(self) -> None:
        with pytest.raises(import_files.ImportFileError):
            parse_inventory_file("stock.xlsx", b"not really a zip archive")


class TestTheRejectionSaysWhy:
    """A generic message is the one Import History keeps, so it has to be specific.

    "That sheet is missing a column StockSync Analytics needs" was true of every
    rejection and useful for none — and because ``imports._fail`` persists the
    message, a failure from last week could not be diagnosed at all.
    """

    def test_it_names_the_missing_column_and_what_was_found(self) -> None:
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file("stock.csv", csv_bytes("Product Name,Price\nThing,9\n"))

        assert caught.value.message == ("That sheet has no SKU column. Found: Product Name, Price.")

    def test_it_names_what_was_found_even_when_nothing_matched(self) -> None:
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file("stock.csv", csv_bytes("Foo,Bar\n1,2\n"))

        assert caught.value.message == "That sheet has no SKU column. Found: Foo, Bar."

    def test_a_wide_sheet_is_summarised_rather_than_dumped(self) -> None:
        """Twenty column names in an error is not a message, it is a haystack."""
        headers = ",".join(f"Col{i}" for i in range(1, 21))
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file("stock.csv", csv_bytes(f"{headers}\n{'1,' * 19}1\n"))

        assert "Col1, Col2, Col3, Col4, Col5, Col6, Col7, Col8 and 12 more." in caught.value.message

    def test_a_wholly_blank_file_is_empty_not_missing_headers(self) -> None:
        """The better of the two messages: there is nothing there at all.

        Worth pinning because it is why the message builder needs no branch for
        an empty header row — blank rows are dropped before the scan, so every
        candidate row has content, and a file with none never gets that far.
        """
        with pytest.raises(EmptyFileError):
            parse_inventory_file("stock.csv", csv_bytes(",,\n,,\n"))

    def test_the_structured_detail_survives_alongside_the_message(self) -> None:
        """The message is for a person; the detail is for the form's field hints."""
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file("stock.csv", csv_bytes("Product Name,Price\nThing,9\n"))

        detail = caught.value.detail
        assert detail["missing"] == ["sku"]
        assert detail["expected_columns"] == ["SKU"]
        assert "Product Name" in detail["found_columns"]
        assert detail["header_row_number"] == 1

    def test_it_reports_the_row_it_judged(self) -> None:
        """With a title row above the table, row 1 is not the header."""
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file(
                "stock.csv", csv_bytes("July export\n\nProduct Name,Price\nThing,9\n")
            )

        assert caught.value.detail["header_row_number"] == 2


class TestExactHeaderBeatsAlias:
    """An exact column name outranks a nickname, wherever each one sits.

    One pass over the aliases used to give every field the first column matching
    any of its spellings, so ``SKU | Units | Total Qty.`` bound total_qty to
    ``Units`` and never read the column named ``Total Qty.``. The imported figure
    depended on column order rather than on the header, and nothing said so.
    """

    def test_total_qty_wins_over_units_to_its_left(self) -> None:
        parsed = parse_inventory_file(
            "s.csv", b"SKU,Units,Total Qty.,Total Count\nA-1,165,404,10\n"
        )

        assert parsed.detected_columns["total_qty"] == "Total Qty."
        assert parsed.rows[0].counts["total_qty"] == 404
        assert parsed.rows[0].quantity == 404

    def test_the_answer_does_not_depend_on_column_order(self) -> None:
        left = parse_inventory_file("s.csv", b"SKU,Units,Total Qty.,Total Count\nA-1,165,404,10\n")
        right = parse_inventory_file("s.csv", b"SKU,Total Qty.,Units,Total Count\nA-1,404,165,10\n")

        assert left.rows[0].counts["total_qty"] == right.rows[0].counts["total_qty"] == 404

    def test_total_count_wins_over_count(self) -> None:
        parsed = parse_inventory_file("s.csv", b"SKU,Count,Total Count,Total Qty.\nA-1,7,10,404\n")

        assert parsed.rows[0].counts["total_count"] == 10

    def test_total_orders_wins_over_orders(self) -> None:
        parsed = parse_inventory_file("s.csv", b"SKU,Orders,Total Orders,Total Qty.\nA-1,3,9,404\n")

        assert parsed.rows[0].counts["total_orders"] == 9

    def test_aliases_still_work_when_the_exact_name_is_absent(self) -> None:
        """Backward compatibility: a sheet with only nicknames still imports."""
        parsed = parse_inventory_file("s.csv", b"SKU,Units,Count,Orders\nA-1,165,7,3\n")

        row = parsed.rows[0]
        assert (
            row.counts["total_qty"],
            row.counts["total_count"],
            row.counts["total_orders"],
        ) == (165, 7, 3)

    def test_two_fields_cannot_claim_one_column(self) -> None:
        """``Qty`` belongs to quantity; total_qty then mirrors it rather than sharing."""
        parsed = parse_inventory_file("s.csv", b"SKU,Qty,Total Count\nA-1,165,10\n")

        assert parsed.detected_columns.get("quantity") == "Qty"
        assert parsed.detected_columns.get("total_qty") is None
        # The mirror keeps a stocked SKU from reporting nothing.
        assert parsed.rows[0].counts["total_qty"] == 165


class TestPartialBeatsComplete:
    """A partial complaint must never be filed as a total loss.

    The pairs used to be decided by substring order alone, which worked only
    when the qualifier sat flush against the family term. "damaged in transit -
    partial" folds to ``damagedintransitpartial``: none of the partial aliases
    are substrings of it, and ``damage`` is, so it went to Item Damage Complete.
    A partial return was written off in full, and the same held for defects and
    for electronics.
    """

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            # The qualifier flush against the term — worked before, must keep working.
            ("Damage Partial", "item_damage_partial"),
            ("partial damage", "item_damage_partial"),
            ("partially damaged", "item_damage_partial"),
            ("Defect Partial", "item_defect_partial"),
            ("partially defective", "item_defect_partial"),
            ("Non Working Partial", "electronics_nonworking_partial"),
            # The qualifier separated from the term — these are the regressions.
            ("damaged in transit - partial", "item_damage_partial"),
            ("item damaged partially", "item_damage_partial"),
            ("product arrived damaged, partial", "item_damage_partial"),
            ("defective - partial", "item_defect_partial"),
            ("item defective partial", "item_defect_partial"),
            ("faulty on arrival, partially", "item_defect_partial"),
            ("electronics not working partially", "electronics_nonworking_partial"),
            ("dead on arrival - partial", "electronics_nonworking_partial"),
        ],
    )
    def test_partial_phrasings_reach_the_partial_column(self, reason: str, expected: str) -> None:
        assert import_files.complaint_for(reason) == expected

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("Damage", "item_damage_complete"),
            ("damaged", "item_damage_complete"),
            ("broken", "item_damage_complete"),
            ("Defective", "item_defect_complete"),
            ("faulty", "item_defect_complete"),
            ("Not Working", "electronics_nonworking_complete"),
            ("DOA", "electronics_nonworking_complete"),
            ("completely damaged", "item_damage_complete"),
            ("fully defective", "item_defect_complete"),
        ],
    )
    def test_an_unqualified_or_complete_reason_still_means_complete(
        self, reason: str, expected: str
    ) -> None:
        """The fix must not drag complete complaints into the partial column."""
        assert import_files.complaint_for(reason) == expected

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("Missing", "missing"),
            ("Missing Part", "missing_part"),
            ("one part missing", "missing_part"),
            ("incomplete", "missing_part"),
            ("Wrong Item Delivered", "item_mismatch_wrong_item"),
            ("wrong parcel", "order_wrong_parcel"),
        ],
    )
    def test_the_unpaired_columns_are_untouched(self, reason: str, expected: str) -> None:
        """`incomplete` contains "complete" and must not be dragged into a pair."""
        assert import_files.complaint_for(reason) == expected

    def test_an_unrecognised_reason_is_still_none(self) -> None:
        assert import_files.complaint_for("customer changed mind") is None

    def test_a_partial_marker_alone_decides_nothing(self) -> None:
        """A qualifier with no family term is not a complaint type."""
        assert import_files.complaint_for("partial") is None

    def test_it_survives_a_whole_complaint_sheet(self) -> None:
        """End to end, because the mapping is only worth anything at import."""
        csv = (
            b"Order No,SKU Code,Reason\n"
            b"INV-1,DD-1,damaged in transit - partial\n"
            b"INV-2,DD-1,Damage\n"
            b"INV-3,DD-1,item defective partial\n"
            b"INV-4,DD-1,Defective\n"
        )
        row = import_files.parse_inventory_file("complaints.csv", csv).rows[0]

        assert row.counts["item_damage_partial"] == 1
        assert row.counts["item_damage_complete"] == 1
        assert row.counts["item_defect_partial"] == 1
        assert row.counts["item_defect_complete"] == 1


class TestTheComplaintDateIsKept:
    """A complaint row carries a date; the importer stores it.

    Without it Complaint Rate % had no dated numerator, so a "rate over 30 days"
    divided an all-time complaint count by 30 days of sales and reported figures
    like 866%.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-07-14", date(2026, 7, 14)),
            ("14-07-2026", date(2026, 7, 14)),
            ("14/07/2026", date(2026, 7, 14)),
            ("14.07.2026", date(2026, 7, 14)),
            ("14-07-26", date(2026, 7, 14)),
            ("2026/07/14", date(2026, 7, 14)),
            ("14-Jul-2026", date(2026, 7, 14)),
            ("14 July 2026", date(2026, 7, 14)),
            ("2026-07-14 09:31:00", date(2026, 7, 14)),
            ("2026-07-14T09:31:00", date(2026, 7, 14)),
            (datetime(2026, 7, 14, 9, 31), date(2026, 7, 14)),
            (date(2026, 7, 14), date(2026, 7, 14)),
        ],
    )
    def test_the_spellings_an_export_actually_writes(self, raw: object, expected: date) -> None:
        assert import_files.parse_complaint_date(raw) == expected

    def test_a_day_first_date_is_read_day_first(self) -> None:
        """This is an Indian business: 03/08/2026 is 3 August, not 8 March.

        Guessing between the two conventions would silently move complaints
        between months, so only one reading is offered.
        """
        assert import_files.parse_complaint_date("03/08/2026") == date(2026, 8, 3)

    @pytest.mark.parametrize("raw", ["", None, "   ", "not a date", "31/31/2026", "0"])
    def test_an_unreadable_date_is_none_not_an_error(self, raw: object) -> None:
        assert import_files.parse_complaint_date(raw) is None

    def test_complaints_are_bucketed_on_their_own_day(self) -> None:
        csv = (
            b"Date,Order No,SKU Code,Reason\n"
            b"2026-01-05,INV-1,DD-1,Missing\n"
            b"2026-01-05,INV-2,DD-1,Damage\n"
            b"2026-02-10,INV-3,DD-1,damaged in transit - partial\n"
        )
        row = import_files.parse_inventory_file("c.csv", csv).rows[0]

        assert set(row.dated_counts) == {date(2026, 1, 5), date(2026, 2, 10)}
        assert row.dated_counts[date(2026, 1, 5)]["missing"] == 1
        assert row.dated_counts[date(2026, 1, 5)]["item_damage_complete"] == 1
        assert row.dated_counts[date(2026, 2, 10)]["item_damage_partial"] == 1
        assert row.undated_complaints == 0

    def test_the_dated_breakdown_sums_to_the_total(self) -> None:
        csv = (
            b"Date,Order No,SKU Code,Reason\n"
            b"2026-01-05,INV-1,DD-1,Missing\n"
            b"2026-01-06,INV-2,DD-1,Damage\n"
            b"2026-01-07,INV-3,DD-1,Defective\n"
        )
        row = import_files.parse_inventory_file("c.csv", csv).rows[0]

        dated = sum(sum(c.values()) for c in row.dated_counts.values())
        assert dated == row.counts["total_count"] == 3

    def test_a_row_with_no_date_is_undated_not_dropped(self) -> None:
        """It still counts towards every total; it just belongs to no window."""
        csv = (
            b"Date,Order No,SKU Code,Reason\n"
            b"2026-01-05,INV-1,DD-1,Missing\n"
            b",INV-2,DD-1,Damage\n"
            b"rubbish,INV-3,DD-1,Defective\n"
        )
        row = import_files.parse_inventory_file("c.csv", csv).rows[0]

        assert sum(sum(c.values()) for c in row.dated_counts.values()) == 1
        assert row.undated_complaints == 2
        assert row.counts["total_count"] == 3
        assert sum(row.counts[f] for f, _ in COMPLAINT_COLUMNS) == 3

    def test_an_export_with_no_date_column_at_all_is_all_undated(self) -> None:
        csv = b"Order No,SKU Code,Reason\nINV-1,DD-1,Missing\nINV-2,DD-1,Damage\n"
        row = import_files.parse_inventory_file("c.csv", csv).rows[0]

        assert row.dated_counts == {}
        assert row.undated_complaints == 2

    def test_an_aggregated_sheet_has_no_dates_and_says_so(self) -> None:
        """Backward compatibility: the format that has never carried a date."""
        csv = b"SKU,Total Qty.,Missing,Item Damage Complete\nDD-1,10,3,4\n"
        row = import_files.parse_inventory_file("s.csv", csv).rows[0]

        assert row.dated_counts == {}
        assert row.undated_complaints == 7
        assert row.counts["missing"] == 3
