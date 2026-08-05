"""Two sheet formats, aliased headers, and the aggregation that joins them.

The point of this module is that a user should never have to rename a column or
reshape a sheet. Two shapes arrive in practice:

* **aggregated** — one row per SKU, counts already totalled
* **complaints** — one row per complaint, which the parser groups by SKU itself

and each arrives with whatever the exporting system calls its columns.
"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.services.import_files import (
    AGGREGATED,
    COMPLAINTS,
    MissingHeadersError,
    SkuIsTwoValuesError,
    complaint_for,
    parse_inventory_file,
    parse_sku,
)


def csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def counts_of(result, sku: str) -> dict[str, int]:
    return next(row.counts for row in result.rows if row.sku == sku)


# ---------------------------------------------------------------------------
# alias mapping
# ---------------------------------------------------------------------------


class TestSkuAliases:
    @pytest.mark.parametrize(
        "header",
        ["SKU", "sku code", "Product SKU", "Item SKU", "Product Code", "Item Code", "SKU_CODE"],
    )
    def test_every_spelling_the_requirement_lists(self, header: str) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(f"{header},Qty\nDD-1,4\n"))

        assert result.rows[0].sku == "DD-1"
        assert result.detected_columns["sku"] == header


class TestCountAliases:
    @pytest.mark.parametrize("header", ["Total Count", "count", "COUNT", "Total_Count"])
    def test_total_count(self, header: str) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(f"SKU,{header}\nDD-1,7\n"))

        assert counts_of(result, "DD-1")["total_count"] == 7

    @pytest.mark.parametrize("header", ["Total Orders", "order count", "Orders", "ORDER_COUNT"])
    def test_total_orders(self, header: str) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(f"SKU,{header}\nDD-1,3\n"))

        assert counts_of(result, "DD-1")["total_orders"] == 3

    @pytest.mark.parametrize("header", ["Total Qty", "qty", "Quantity", "units", "Total Qty."])
    def test_total_qty(self, header: str) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(f"SKU,{header}\nDD-1,9\n"))

        row = result.rows[0]
        assert row.quantity == 9
        # Whatever it was called, it is the sheet's Total Qty.
        assert row.counts["total_qty"] == 9

    def test_an_explicit_total_qty_wins_over_a_quantity_column(self) -> None:
        """Both aliases resolve, so the specific column must not be shadowed."""
        result = parse_inventory_file("s.csv", csv_bytes("SKU,Quantity,Total Qty\nDD-1,5,99\n"))

        assert result.rows[0].quantity == 99
        assert result.rows[0].counts["total_qty"] == 99


class TestOnlySkuIsRequired:
    def test_a_single_column_sheet_imports(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes("SKU\nDD-1\nDD-2\n"))

        assert [row.sku for row in result.rows] == ["DD-1", "DD-2"]
        assert all(row.quantity == 0 for row in result.rows)
        assert not result.rejected

    def test_any_subset_of_the_optional_columns_imports(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes("SKU,Missing,Total Orders\nDD-1,2,5\n"))

        counts = counts_of(result, "DD-1")
        assert counts["missing"] == 2
        assert counts["total_orders"] == 5
        assert counts["item_damage_complete"] == 0

    def test_a_sheet_with_no_sku_is_still_refused(self) -> None:
        with pytest.raises(MissingHeadersError) as caught:
            parse_inventory_file("s.csv", csv_bytes("Total Qty,Reason\n4,Damage\n"))

        assert caught.value.detail["missing"] == ["sku"]


# ---------------------------------------------------------------------------
# reason mapping
# ---------------------------------------------------------------------------


class TestReasonMapping:
    @pytest.mark.parametrize(
        ("reason", "field_name"),
        [
            # The five the requirement spells out.
            ("Damage", "item_damage_complete"),
            ("Non Working", "electronics_nonworking_complete"),
            ("Missing", "missing"),
            ("Missing Part", "missing_part"),
            ("Wrong Item", "item_mismatch_wrong_item"),
            # And the rest of the ten columns.
            ("Defect", "item_defect_complete"),
            ("Defect Partial", "item_defect_partial"),
            ("Damage Partial", "item_damage_partial"),
            ("Wrong Parcel", "order_wrong_parcel"),
            ("Non Working Partial", "electronics_nonworking_partial"),
        ],
    )
    def test_the_documented_reasons(self, reason: str, field_name: str) -> None:
        assert complaint_for(reason) == field_name

    @pytest.mark.parametrize(
        "reason", ["damage", "DAMAGE", "Damaged", "  damage  ", "da-mage", "Da_Mage"]
    )
    def test_case_and_punctuation_fold_away(self, reason: str) -> None:
        assert complaint_for(reason) == "item_damage_complete"

    def test_the_specific_reason_beats_the_general_one(self) -> None:
        """ "missing part" contains "missing"; testing the general case first
        would file every missing part under Missing."""
        assert complaint_for("Missing Part") == "missing_part"
        assert complaint_for("Missing") == "missing"

    @pytest.mark.parametrize(
        ("reason", "field_name"),
        [
            ("Item damaged in transit", "item_damage_complete"),
            ("Customer says missing part in box", "missing_part"),
            ("unit not working at all", "electronics_nonworking_complete"),
            ("wrong item delivered to customer", "item_mismatch_wrong_item"),
        ],
    )
    def test_a_reason_in_a_sentence_still_maps(self, reason: str, field_name: str) -> None:
        assert complaint_for(reason) == field_name

    @pytest.mark.parametrize("reason", ["", None, "   ", "Customer changed mind", "N/A"])
    def test_an_unrecognised_reason_is_none_rather_than_a_guess(self, reason: object) -> None:
        assert complaint_for(reason) is None


# ---------------------------------------------------------------------------
# Format 2 — raw complaint rows
# ---------------------------------------------------------------------------


RAW = (
    "Date,Order No,SKU Code,Reason,Employee\n"
    "2026-07-01,ORD-1,DD-100,Damage,anita\n"
    "2026-07-01,ORD-1,DD-100,Missing Part,anita\n"
    "2026-07-02,ORD-2,DD-100,Non Working,ravi\n"
    "2026-07-02,ORD-3,DD-200,Wrong Item,ravi\n"
    "2026-07-03,ORD-4,DD-200,Missing,sam\n"
)


class TestRawComplaintFormat:
    def test_it_is_detected_from_the_reason_column(self) -> None:
        assert parse_inventory_file("s.csv", csv_bytes(RAW)).sheet_format == COMPLAINTS

    def test_an_aggregated_sheet_is_not_mistaken_for_it(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes("SKU,Total Qty,Missing\nDD-1,5,2\n"))

        assert result.sheet_format == AGGREGATED

    def test_rows_are_grouped_into_one_row_per_sku(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(RAW))

        assert result.rows_read == 5
        assert [row.sku for row in result.rows] == ["DD-100", "DD-200"]

    def test_total_count_is_the_number_of_complaint_rows(self) -> None:
        counts = counts_of(parse_inventory_file("s.csv", csv_bytes(RAW)), "DD-100")

        assert counts["total_count"] == 3

    def test_total_orders_counts_distinct_order_numbers(self) -> None:
        """Two faulty items in one parcel is one order, two rows."""
        counts = counts_of(parse_inventory_file("s.csv", csv_bytes(RAW)), "DD-100")

        assert counts["total_orders"] == 2  # ORD-1 twice, ORD-2 once

    def test_total_qty_is_one_per_row_without_a_quantity_column(self) -> None:
        counts = counts_of(parse_inventory_file("s.csv", csv_bytes(RAW)), "DD-100")

        assert counts["total_qty"] == 3

    def test_a_quantity_column_is_summed_instead(self) -> None:
        body = "Order No,SKU,Reason,Qty\nORD-1,DD-1,Damage,3\nORD-2,DD-1,Missing,2\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert counts_of(result, "DD-1")["total_qty"] == 5
        assert result.rows[0].quantity == 5

    def test_each_reason_lands_in_its_own_column(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(RAW))

        first = counts_of(result, "DD-100")
        assert first["item_damage_complete"] == 1
        assert first["missing_part"] == 1
        assert first["electronics_nonworking_complete"] == 1
        second = counts_of(result, "DD-200")
        assert second["item_mismatch_wrong_item"] == 1
        assert second["missing"] == 1

    def test_an_unmapped_reason_still_counts_towards_the_totals(self) -> None:
        """Losing the row entirely would under-count orders as well as complaints."""
        body = "Order No,SKU,Reason\nORD-1,DD-1,Customer changed mind\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        counts = counts_of(result, "DD-1")
        assert counts["total_count"] == 1
        assert counts["total_orders"] == 1
        assert counts["total_qty"] == 1
        assert (
            sum(counts[f] for f in counts if f.startswith(("item_", "missing", "electronics"))) == 0
        )
        assert result.unmapped_reasons == {"Customer changed mind": 1}

    def test_unmapped_reasons_are_counted_and_ordered_by_frequency(self) -> None:
        body = (
            "SKU,Reason\nDD-1,Late delivery\nDD-1,Late delivery\nDD-2,Changed mind\nDD-1,Damage\n"
        )
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert list(result.unmapped_reasons.items()) == [("Late delivery", 2), ("Changed mind", 1)]

    def test_without_an_order_column_every_row_is_its_own_order(self) -> None:
        body = "SKU,Reason\nDD-1,Damage\nDD-1,Missing\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert counts_of(result, "DD-1")["total_orders"] == 2

    def test_a_blank_order_number_is_not_folded_into_one_order(self) -> None:
        """Nothing distinguishes blanks, so treating them as one would under-count."""
        body = "Order No,SKU,Reason\n,DD-1,Damage\n,DD-1,Missing\nORD-1,DD-1,Damage\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert counts_of(result, "DD-1")["total_orders"] == 3

    def test_a_row_with_no_sku_is_rejected_and_named(self) -> None:
        body = "Order No,SKU,Reason\nORD-1,,Damage\nORD-2,DD-1,Missing\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert [row.sku for row in result.rows] == ["DD-1"]
        assert result.rejected[0].reason == "missing_sku"
        assert result.rejected[0].row_number == 2

    def test_the_grouping_is_reported_as_merged_rows(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(RAW))

        assert result.rows_merged == 3  # 5 rows -> 2 SKUs
        by_sku = {group.sku: group for group in result.duplicates}
        assert by_sku["DD-100"].rows == [2, 3, 4]

    def test_aliased_headers_work_in_this_format_too(self) -> None:
        body = (
            "Complaint Date,Invoice No,Product Code,Issue Type,Handled By\n"
            "2026-07-01,INV-1,DD-1,Damage,anita\n"
        )
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert result.sheet_format == COMPLAINTS
        assert result.rows[0].sku == "DD-1"
        assert counts_of(result, "DD-1")["item_damage_complete"] == 1


# ---------------------------------------------------------------------------
# every doorway
# ---------------------------------------------------------------------------


class TestTheSameRulesAcrossFileTypes:
    def test_csv(self) -> None:
        result = parse_inventory_file("s.csv", csv_bytes(RAW))

        assert result.sheet_format == COMPLAINTS
        assert len(result.rows) == 2

    def test_excel(self) -> None:
        rows: list[list[object]] = [["Date", "Order No", "SKU Code", "Reason", "Employee"]]
        rows += [
            ["2026-07-01", "ORD-1", "DD-100", "Damage", "anita"],
            ["2026-07-01", "ORD-1", "DD-100", "Missing Part", "anita"],
            ["2026-07-02", "ORD-2", "DD-100", "Non Working", "ravi"],
            ["2026-07-02", "ORD-3", "DD-200", "Wrong Item", "ravi"],
            ["2026-07-03", "ORD-4", "DD-200", "Missing", "sam"],
        ]
        result = parse_inventory_file("s.xlsx", xlsx_bytes(rows))

        assert result.sheet_format == COMPLAINTS
        assert counts_of(result, "DD-100")["total_orders"] == 2
        assert counts_of(result, "DD-100")["missing_part"] == 1

    def test_a_google_sheet_arrives_as_csv_and_is_read_identically(self) -> None:
        """The Google Sheet path downloads a CSV export, so it is the CSV path."""
        from_sheet = parse_inventory_file("google-sheet-1AbC.csv", csv_bytes(RAW))
        from_upload = parse_inventory_file("s.csv", csv_bytes(RAW))

        assert from_sheet.sheet_format == from_upload.sheet_format
        assert [r.counts for r in from_sheet.rows] == [r.counts for r in from_upload.rows]


class TestDuplicateSkusMerge:
    def test_an_aggregated_sheet_sums_every_column(self) -> None:
        body = (
            "SKU,Total Count,Total Orders,Total Qty,Missing,Item Damage Complete\n"
            "DD-1,10,4,100,1,2\n"
            "DD-1,5,3,50,3,4\n"
        )
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert len(result.rows) == 1
        counts = counts_of(result, "DD-1")
        assert counts["total_count"] == 15
        assert counts["total_orders"] == 7
        assert counts["total_qty"] == 150
        assert counts["missing"] == 4
        assert counts["item_damage_complete"] == 6
        assert result.rows[0].quantity == 150

    def test_the_sku_is_matched_after_normalisation(self) -> None:
        """ "DD-1", "dd 1" and "dd_1" are the same SKU."""
        body = "SKU,Total Qty\nDD-1,10\ndd 1,5\nDD_1,1\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert len(result.rows) == 1
        assert result.rows[0].counts["total_qty"] == 16
        # The sheet's own first spelling is kept — it is what the user recognises.
        assert result.rows[0].sku == "DD-1"

    def test_a_complaint_sheet_merges_the_same_way(self) -> None:
        body = "Order No,SKU,Reason\nORD-1,DD-1,Damage\nORD-2,dd 1,Missing\n"
        result = parse_inventory_file("s.csv", csv_bytes(body))

        assert len(result.rows) == 1
        counts = counts_of(result, "DD-1")
        assert counts["total_count"] == 2
        assert counts["item_damage_complete"] == 1
        assert counts["missing"] == 1


class TestSkuValidation:
    """What arrives in the SKU cell, and which problems can be repaired.

    A production workspace held
    ``'14124_smart_floor_cleaning_robot_1pc\n4710_airplane_launcher_gun_toy'`` —
    two products pasted into one cell. It imported as a single SKU whose
    normalised key matched neither, so both were silently absent from every
    figure and the row rendered across two lines.
    """

    def test_an_ordinary_sku_is_untouched(self) -> None:
        assert parse_sku("DD-1001") == "DD-1001"

    def test_surrounding_whitespace_is_still_stripped(self) -> None:
        assert parse_sku("  DD-1001\t") == "DD-1001"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("DD  1001", "DD 1001"),
            ("DD   1001", "DD 1001"),
            ("DD\t1001", "DD 1001"),
            ("DD \t 1001", "DD 1001"),
            ("A  B  C", "A B C"),
        ],
    )
    def test_a_run_of_spaces_collapses_to_one(self, raw: str, expected: str) -> None:
        """One SKU typed loosely is still one SKU — this is tidying, not merging."""
        assert parse_sku(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["a\nb", "a\r\nb", "a\rb", "a\x0bb", "a\x0cb", "a\u2028b", "a\u2029b"],
    )
    def test_a_line_break_is_refused_rather_than_repaired(self, raw: str) -> None:
        """Collapsing would mint a SKU matching neither half and lose both."""
        with pytest.raises(SkuIsTwoValuesError):
            parse_sku(raw)

    def test_an_empty_cell_is_still_empty_rather_than_an_error(self) -> None:
        assert parse_sku("") == ""
        assert parse_sku(None) == ""
        assert parse_sku("   ") == ""


class TestSkuValidationThroughTheParser:
    LINE_BROKEN = "14124_smart_floor_cleaning_robot_1pc\n4710_airplane_launcher_gun_toy"

    def test_an_aggregated_row_with_two_skus_is_rejected(self) -> None:
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes(f'SKU,Total Qty.\nDD-1,10\n"{self.LINE_BROKEN}",20\nDD-2,30\n'),
        )

        assert [row.sku for row in result.rows] == ["DD-1", "DD-2"]
        assert [r.reason for r in result.rejected] == ["invalid_sku"]

    def test_the_rejection_names_both_halves(self) -> None:
        """So the row can be found and split in the source sheet."""
        result = parse_inventory_file(
            "stock.csv", csv_bytes(f'SKU,Total Qty.\n"{self.LINE_BROKEN}",20\n')
        )

        detail = result.rejected[0].detail
        assert "14124_smart_floor_cleaning_robot_1pc" in detail
        assert "4710_airplane_launcher_gun_toy" in detail
        assert "Split it across two rows" in detail

    def test_the_rest_of_the_file_still_imports(self) -> None:
        """One bad row is a rejected row, not a failed upload."""
        result = parse_inventory_file(
            "stock.csv",
            csv_bytes(f'SKU,Total Qty.\nDD-1,10\n"{self.LINE_BROKEN}",20\nDD-2,30\n'),
        )

        assert result.rows_read == 3
        assert len(result.rows) == 2
        assert result.rejected[0].row_number == 3

    def test_a_complaint_export_row_with_two_skus_is_rejected_too(self) -> None:
        result = parse_inventory_file(
            "complaints.csv",
            csv_bytes(
                "Date,Order No,SKU Code,Reason\n"
                "2026-08-01,O-1,DD-1,Missing\n"
                f'2026-08-01,O-2,"{self.LINE_BROKEN}",Missing\n'
            ),
        )

        assert [row.sku for row in result.rows] == ["DD-1"]
        assert [r.reason for r in result.rejected] == ["invalid_sku"]

    def test_a_spaced_sku_imports_as_one_value(self) -> None:
        result = parse_inventory_file("stock.csv", csv_bytes('SKU,Total Qty.\n"DD  1001",10\n'))

        assert [row.sku for row in result.rows] == ["DD 1001"]
        assert result.rejected == []

    def test_two_spellings_of_the_same_sku_still_merge(self) -> None:
        """Collapsing must not create a new SKU: normalisation drops spaces
        anyway, so `DD  1001` and `DD-1001` remain the same row."""
        result = parse_inventory_file(
            "stock.csv", csv_bytes('SKU,Total Qty.\n"DD  1001",10\nDD-1001,15\n')
        )

        assert len(result.rows) == 1
        assert result.rows[0].counts["total_qty"] == 25

    def test_an_excel_cell_with_a_line_break_is_rejected_as_well(self) -> None:
        result = parse_inventory_file(
            "stock.xlsx",
            xlsx_bytes([["SKU", "Total Qty."], ["DD-1", 10], [self.LINE_BROKEN, 20]]),
        )

        assert [row.sku for row in result.rows] == ["DD-1"]
        assert [r.reason for r in result.rejected] == ["invalid_sku"]
