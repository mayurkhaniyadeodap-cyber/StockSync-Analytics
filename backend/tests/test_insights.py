"""The Analytics derivations, tested against literal facts.

No database and no HTTP: ``services.insights`` takes a list and returns numbers,
which is the whole reason it was written that way. A wrong median or an
off-by-one rank shows up here as arithmetic rather than as a rendering bug three
layers up.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.models import COMPLAINT_COLUMNS
from app.repositories.analytics import SkuFact
from app.services import insights


def fact(
    sku: str,
    *,
    qty: int = 0,
    orders: int = 0,
    sales: int = 0,
    complaints: dict[str, int] | None = None,
) -> SkuFact:
    """One SKU. `qty` fills both quantity fields, as the importer does.

    ``complaints`` is the sheet's whole tally for this SKU — no date filter is
    applied to it anywhere. ``sales`` is the selected range's.
    """
    by_category = {attribute: 0 for attribute, _ in COMPLAINT_COLUMNS}
    by_category.update(complaints or {})
    return SkuFact(
        sku=sku,
        sku_normalized=sku.replace("-", "").lower(),
        quantity=qty,
        total_qty=qty,
        total_orders=orders,
        total_count=0,
        complaints=by_category,
        total_complaints=sum(by_category.values()),
        shopify_sales=sales,
    )


DEFECT = COMPLAINT_COLUMNS[0][0]
DAMAGE = COMPLAINT_COLUMNS[2][0]


class TestTheComplaintCount:
    """The count survives; the rate does not."""

    def test_complaints_are_the_sum_of_every_category(self) -> None:
        row = fact("A", qty=1000, complaints={DEFECT: 6, DAMAGE: 4})

        assert row.total_complaints == 10

    def test_a_sku_carries_no_complaint_rate(self) -> None:
        """Complaint Rate % was removed from the project — field, column and
        helper. Nothing derives one, here or anywhere else."""
        row = fact("A", qty=1000, complaints={DEFECT: 6})

        assert not hasattr(row, "complaint_rate")

    def test_the_helper_is_gone_too(self) -> None:
        from app.core import calc

        assert not hasattr(calc, "complaint_rate_pct")


class TestShopifySalesShare:
    """On the table: ``(a SKU's sales ÷ the imported SKUs' sales) × 100``."""

    FACTS: ClassVar[list[SkuFact]] = [
        fact("A", qty=100, sales=250),
        fact("B", qty=100, sales=750),
    ]

    def test_a_sku_is_a_share_of_the_imported_total(self) -> None:
        rows, _ = insights.performance(self.FACTS, limit=10, offset=0)

        assert [r.shopify_sales_pct for r in rows] == [75.0, 25.0]

    def test_the_column_sums_to_one_hundred(self) -> None:
        """The table is a composition of one whole, which is the point."""
        rows, _ = insights.performance(self.FACTS, limit=10, offset=0)

        assert sum(r.shopify_sales_pct for r in rows) == 100.0

    def test_no_row_can_exceed_one_hundred(self) -> None:
        """Every numerator is part of the denominator, so none can."""
        rows, _ = insights.performance(self.FACTS, limit=10, offset=0)

        assert all(0 <= r.shopify_sales_pct <= 100 for r in rows)

    def test_the_store_total_cannot_reach_this_column(self) -> None:
        """The denominator is derived from the facts, so no caller supplies it —
        which is what stops the table and the cards drifting apart by accident.
        """
        import inspect

        assert "all_units" not in inspect.signature(insights.performance).parameters

    def test_filtering_does_not_change_the_denominator(self) -> None:
        """Narrowing to one SKU must not make it 100% of itself."""
        rows, _ = insights.performance(
            self.FACTS, filters=insights.Filters(search="A"), limit=10, offset=0
        )

        assert [r.sku for r in rows] == ["A"]
        assert rows[0].shopify_sales_pct == 25.0

    def test_nothing_sold_is_zero_not_a_division(self) -> None:
        rows, _ = insights.performance([fact("A", qty=10), fact("B", qty=10)], limit=10, offset=0)

        assert [r.shopify_sales_pct for r in rows] == [0.0, 0.0]

    def test_one_helper_computes_it(self) -> None:
        from app.core.calc import sales_pct

        rows, _ = insights.performance(self.FACTS, limit=10, offset=0)
        assert rows[0].shopify_sales_pct == sales_pct(750, 1000)


class TestThePortfolioFiguresMatchTheColumns:
    """A card above a column and the column must divide the same way."""

    FACTS: ClassVar[list[SkuFact]] = [
        fact("A", qty=1000, sales=1000, complaints={DEFECT: 5}),
        fact("B", qty=3000, sales=3000, complaints={DEFECT: 25}),
        fact("C", qty=0, sales=0, complaints={DAMAGE: 10}),
    ]

    def test_the_kpi_totals_the_complaints(self) -> None:
        k = insights.kpis(self.FACTS, all_units=insights.matched_units(self.FACTS))

        assert k.total_complaints == 40
        assert k.total_qty == 4000

    def test_the_complaints_panel_agrees_with_the_kpi(self) -> None:
        k = insights.kpis(self.FACTS, all_units=insights.matched_units(self.FACTS))
        c = insights.complaints(self.FACTS, all_units=insights.matched_units(self.FACTS))

        assert c.total_complaints == k.total_complaints

    def test_the_sales_share_card_is_a_share_of_the_store(self) -> None:
        """The imported SKUs' 4,000 units against the store's 10,000."""
        k = insights.kpis(self.FACTS, all_units=10_000)

        assert k.shopify_sales == 4000
        assert k.shopify_sales_pct == 40.0

    def test_an_empty_workspace_is_zero_not_a_division(self) -> None:
        k = insights.kpis([], all_units=0)

        assert k.shopify_sales_pct == 0.0
        assert k.total_complaints == 0


class TestKpis:
    def test_the_eight_figures(self) -> None:
        facts = [
            fact("A", qty=100, orders=200, sales=80, complaints={DEFECT: 4}),
            fact("B", qty=50, orders=100, sales=20, complaints={DAMAGE: 2}),
        ]

        k = insights.kpis(facts, all_units=200)

        assert k.total_skus == 2
        assert k.total_qty == 150
        assert k.shopify_sales == 100
        assert k.shopify_sales_pct == 50.0  # 100 of the store's 200 units
        assert k.total_orders == 300
        assert k.total_complaints == 6
        assert k.avg_sales_per_sku == 50.0  # 100 units / 2 SKUs
        assert k.shopify_sales_all == 200

    def test_an_empty_workspace_is_zeroes_not_a_crash(self) -> None:
        k = insights.kpis([], all_units=0)

        assert k.total_skus == 0
        assert k.avg_sales_per_sku == 0.0
        assert k.total_complaints == 0

    def test_no_shopify_sales_leaves_the_percentage_at_zero(self) -> None:
        """Nothing synced yet: the sheet still has figures, the share has none."""
        k = insights.kpis([fact("A", qty=10, orders=5)], all_units=0)

        assert k.total_qty == 10
        assert k.shopify_sales_pct == 0.0

    def test_the_cards_agree_with_the_table(self) -> None:
        """The claim the one-read design exists to make."""
        facts = [
            fact("A", qty=9, orders=40, sales=30, complaints={DEFECT: 2}),
            fact("B", qty=4, orders=10, sales=5, complaints={DAMAGE: 1}),
            fact("C", qty=100, orders=0, sales=0),
        ]

        k = insights.kpis(facts, all_units=35)
        rows, total = insights.performance(facts, limit=100)

        assert total == k.total_skus
        assert sum(r.total_complaints for r in rows) == k.total_complaints
        assert sum(r.shopify_sales for r in rows) == k.shopify_sales
        assert sum(r.total_qty for r in rows) == k.total_qty
        assert sum(r.total_orders for r in rows) == k.total_orders


class TestSales:
    def test_highest_and_lowest(self) -> None:
        facts = [fact("A", sales=10), fact("B", sales=90), fact("C", sales=50)]

        s = insights.sales(facts, all_units=150)

        assert s.highest is not None and s.highest.sku == "B"
        assert s.lowest is not None and s.lowest.sku == "A"
        assert s.shopify_sales == 150
        assert s.shopify_sales_pct == 100.0

    def test_the_lowest_seller_is_ranked_last_not_first(self) -> None:
        """Rank 1 must mean "the best" in every table on the page."""
        facts = [fact("A", sales=10), fact("B", sales=90), fact("C", sales=50)]

        s = insights.sales(facts, all_units=150)

        assert s.lowest is not None
        assert s.lowest.rank == 3

    def test_nothing_sold_means_no_highest(self) -> None:
        """An absent finding, not a zero row that reads as one."""
        s = insights.sales([fact("A", qty=5), fact("B", qty=3)], all_units=0)

        assert s.highest is None
        assert s.top == []

    def test_distribution_slices_sum_to_the_whole(self) -> None:
        """With more SKUs than the chart shows, the remainder is explicit."""
        facts = [fact(f"SKU-{i}", sales=i) for i in range(1, 21)]
        total = sum(range(1, 21))

        s = insights.sales(facts, all_units=total)

        assert sum(slice_.count for slice_ in s.distribution) == total
        assert s.distribution[-1].field_name == "__other__"
        assert "10 SKUs" in s.distribution[-1].label
        assert round(sum(slice_.share_pct for slice_ in s.distribution)) == 100

    def test_no_remainder_slice_when_everything_fits(self) -> None:
        s = insights.sales([fact("A", sales=6), fact("B", sales=4)], all_units=10)

        assert [slice_.field_name for slice_ in s.distribution] == ["a", "b"]

    def test_equal_sales_rank_deterministically(self) -> None:
        facts = [fact("Z-1", sales=5), fact("A-1", sales=5)]

        assert [r.sku for r in insights.sales(facts, all_units=10).top] == ["A-1", "Z-1"]


class TestComplaints:
    def test_every_category_is_reported_including_the_empty_ones(self) -> None:
        """An absent category is information: nothing of that kind went wrong."""
        c = insights.complaints([fact("A", orders=10, complaints={DEFECT: 1})], all_units=0)

        assert len(c.categories) == len(COMPLAINT_COLUMNS)
        assert c.categories[0].field_name == DEFECT
        assert c.categories[0].share_pct == 100.0
        assert all(cat.count == 0 for cat in c.categories[1:])

    def test_categories_are_largest_first(self) -> None:
        c = insights.complaints(
            [fact("A", orders=10, complaints={DEFECT: 1, DAMAGE: 9})], all_units=0
        )

        assert [cat.field_name for cat in c.categories[:2]] == [DAMAGE, DEFECT]

    def test_most_complained_is_by_count(self) -> None:
        facts = [
            fact("A", orders=10_000, complaints={DEFECT: 40}),
            fact("B", orders=10, complaints={DEFECT: 5}),
        ]

        c = insights.complaints(facts, all_units=0)

        assert c.most_complained is not None and c.most_complained.sku == "A"

    def test_a_clean_store_has_no_most_complained(self) -> None:
        c = insights.complaints([fact("A", orders=50, sales=10)], all_units=10)

        assert c.most_complained is None
        assert c.top_skus == []
        assert c.total_complaints == 0


class TestRankings:
    def test_three_tables_each_ranked_from_one(self) -> None:
        facts = [
            fact("HIGH", qty=100, sales=100, orders=100, complaints={DEFECT: 1}),
            fact("MID", qty=100, sales=50, orders=100, complaints={DEFECT: 20}),
            fact("LOW", qty=100, sales=1, orders=100),
        ]

        r = insights.rankings(facts, all_units=151)

        assert [row.sku for row in r.top_selling] == ["HIGH", "MID", "LOW"]
        assert [row.rank for row in r.top_selling] == [1, 2, 3]
        assert [row.sku for row in r.lowest_selling] == ["LOW", "MID", "HIGH"]
        assert [row.rank for row in r.lowest_selling] == [1, 2, 3]
        # By rate, not by count: MID's 20% beats HIGH's 1%.
        assert [row.sku for row in r.highest_complaint] == ["MID", "HIGH"]

    def test_zero_sellers_are_left_out_of_top_selling(self) -> None:
        r = insights.rankings([fact("A", sales=5), fact("B", qty=9)], all_units=5)

        assert [row.sku for row in r.top_selling] == ["A"]
        # …but they are the point of the lowest-selling table.
        assert [row.sku for row in r.lowest_selling] == ["B", "A"]

    def test_a_sku_with_complaints_is_ranked_whatever_else_it_lacks(self) -> None:
        """Ranked on the count, so no order, sale or stock figure is needed."""
        r = insights.rankings([fact("A", orders=0, complaints={DEFECT: 99})], all_units=0)

        assert [row.sku for row in r.highest_complaint] == ["A"]

    def test_a_sku_with_no_complaints_is_not_ranked(self) -> None:
        r = insights.rankings([fact("A", qty=10, sales=5)], all_units=5)

        assert r.highest_complaint == []

    def test_rankings_are_capped(self) -> None:
        facts = [fact(f"SKU-{i:03d}", sales=i) for i in range(1, 41)]

        r = insights.rankings(facts, all_units=1000)

        assert len(r.top_selling) == insights.TOP_N
        assert len(r.lowest_selling) == insights.TOP_N


class TestInventoryInsights:
    def test_cuts_are_made_at_the_medians(self) -> None:
        facts = [
            fact("BIG-SLOW", qty=1000, sales=1),
            fact("SMALL-FAST", qty=1, sales=1000),
            fact("MIDDLE", qty=10, sales=10),
        ]

        i = insights.inventory_insights(facts, all_units=1011)

        assert i.median_qty == 10.0
        assert i.median_sales == 10.0
        assert [row.sku for row in i.high_stock_low_sales] == ["BIG-SLOW"]
        assert [row.sku for row in i.low_stock_high_sales] == ["SMALL-FAST"]

    def test_zero_sales_needs_stock_to_be_a_finding(self) -> None:
        """No stock and no sales is not a problem; stock and no sales is."""
        facts = [fact("HELD", qty=40), fact("EMPTY", qty=0), fact("SOLD", qty=5, sales=3)]

        i = insights.inventory_insights(facts, all_units=3)

        assert [row.sku for row in i.zero_sales] == ["HELD"]
        assert i.zero_sales_total == 1

    def test_the_total_is_reported_beyond_the_cap(self) -> None:
        """A capped list cannot say how many there are; the count must."""
        facts = [fact(f"DEAD-{i:03d}", qty=i + 1) for i in range(25)]

        i = insights.inventory_insights(facts, all_units=0)

        assert len(i.zero_sales) == insights.TOP_N
        assert i.zero_sales_total == 25

    def test_most_complaints_is_ordered_by_count(self) -> None:
        """Ranked by the count, since there is no rate left to rank by."""
        facts = [
            fact("FEW", qty=1000, sales=1000, complaints={DEFECT: 5}),
            fact("MANY", qty=10, sales=10, complaints={DEFECT: 20}),
        ]

        i = insights.inventory_insights(facts, all_units=1010)

        assert [row.sku for row in i.most_complaints] == ["MANY", "FEW"]

    def test_an_empty_workspace_returns_empty_lists(self) -> None:
        i = insights.inventory_insights([], all_units=0)

        assert i.zero_sales == []
        assert i.median_qty == 0.0


class TestStatus:
    @pytest.mark.parametrize(
        ("orders", "complaints", "qty", "sales", "expected"),
        [
            # Complaint *counts* now — there is no rate to read against.
            (100, 40, 100, 50, "critical"),  # well past the critical line
            (100, 25, 100, 50, "critical"),  # exactly at it
            (100, 12, 100, 50, "attention"),  # past the attention line
            (100, 8, 100, 50, "attention"),  # exactly at it
            (100, 0, 40, 0, "attention"),  # stock that will not move
            (100, 0, 100, 50, "excellent"),  # sells, no complaints
            (100, 5, 100, 100, "good"),  # under attention, over excellent
            (0, 0, 0, 0, "good"),  # nothing known either way
        ],
    )
    def test_the_rules(
        self, orders: int, complaints: int, qty: int, sales: int, expected: str
    ) -> None:
        row = fact("A", qty=qty, orders=orders, sales=sales, complaints={DEFECT: complaints})

        assert insights.status_for(row, median_sales=10.0) == expected

    def test_a_defective_bestseller_is_still_critical(self) -> None:
        """Selling well does not offset sixty complaints."""
        row = fact("A", qty=100, orders=1000, sales=5000, complaints={DEFECT: 60})

        assert insights.status_for(row, median_sales=10.0) == "critical"

    def test_excellent_needs_to_beat_the_median(self) -> None:
        """Below-median sales with a clean record is good, not excellent."""
        row = fact("A", qty=5, orders=100, sales=2)

        assert insights.status_for(row, median_sales=50.0) == "good"


class TestQuickInsights:
    def test_a_finding_with_nothing_behind_it_is_omitted(self) -> None:
        """No cards at all rather than six cards reading "—"."""
        assert insights.quick_insights([], all_units=0) == []

    def test_best_performing_requires_a_clean_record(self) -> None:
        facts = [
            fact("DIRTY", qty=10, orders=100, sales=500, complaints={DEFECT: 1}),
            fact("CLEAN", qty=10, orders=100, sales=200),
        ]

        found = {i.key: i for i in insights.quick_insights(facts, all_units=700)}

        assert found["best"].sku == "CLEAN"
        assert found["complaints"].sku == "DIRTY"

    def test_fastest_selling_is_turnover_not_volume(self) -> None:
        """Sold its whole shelf twice over beats sold more from a bigger one."""
        facts = [
            fact("BULK", qty=10_000, sales=2_000),  # 0.2x
            fact("TURNS", qty=10, sales=20),  # 2.0x
        ]

        found = {i.key: i for i in insights.quick_insights(facts, all_units=2020)}

        assert found["fastest"].sku == "TURNS"
        assert "2.0×" in found["fastest"].value

    def test_no_sales_counts_them_all_and_names_the_worst(self) -> None:
        facts = [
            fact("DEAD-BIG", qty=900),
            fact("DEAD-SMALL", qty=5),
            fact("ALIVE", qty=5, sales=50),
        ]

        found = {i.key: i for i in insights.quick_insights(facts, all_units=50)}

        assert found["nosales"].value == "2 SKUs"
        assert "DEAD-BIG" in found["nosales"].note

    def test_no_dead_stock_means_no_card(self) -> None:
        found = {i.key for i in insights.quick_insights([fact("A", qty=5, sales=5)], all_units=5)}

        assert "nosales" not in found

    def test_restock_needs_sales_beyond_the_stock_held(self) -> None:
        facts = [
            fact("THIN", qty=2, sales=400),
            fact("FINE", qty=900, sales=300),
        ]

        found = {i.key: i for i in insights.quick_insights(facts, all_units=700)}

        assert found["restock"].sku == "THIN"

    def test_every_icon_is_one_the_client_has(self) -> None:
        """A card with a name the client cannot draw renders a blank square."""
        known = {"check", "warn", "chart", "box", "bell", "x", "layers", "clock"}
        facts = [
            fact("A", qty=10, orders=100, sales=500),
            fact("B", qty=10, orders=100, sales=2, complaints={DEFECT: 30}),
            fact("C", qty=90),
        ]

        for insight in insights.quick_insights(facts, all_units=502):
            assert insight.icon in known, insight.key


#: One SKU of each status: AAA excellent, BBB and DDD critical, CCC attention.
FACTS = [
    fact("AAA", qty=100, orders=100, sales=500),
    fact("BBB", qty=50, orders=100, sales=200, complaints={DEFECT: 30}),  # critical
    fact("CCC", qty=900, orders=0, sales=0),  # attention: stock, no sales
    fact("DDD", qty=1, orders=10, sales=1, complaints={DAMAGE: 10}),  # attention
]


class TestPerformance:
    def test_default_sort_is_sales_descending(self) -> None:
        rows, total = insights.performance(FACTS)

        assert [r.sku for r in rows] == ["AAA", "BBB", "DDD", "CCC"]
        assert total == 4

    @pytest.mark.parametrize(
        ("sort", "descending", "expected"),
        [
            ("sku", False, ["AAA", "BBB", "CCC", "DDD"]),
            ("total_qty", True, ["CCC", "AAA", "BBB", "DDD"]),
            ("total_orders", True, ["AAA", "BBB", "DDD", "CCC"]),
            # Worst first: critical before attention before good.
            ("status", False, ["BBB", "CCC", "DDD", "AAA"]),
        ],
    )
    def test_sorting(self, sort: str, descending: bool, expected: list[str]) -> None:
        rows, _ = insights.performance(FACTS, sort=sort, descending=descending)

        assert [r.sku for r in rows] == expected

    def test_an_unknown_sort_key_falls_back_rather_than_raising(self) -> None:
        rows, _ = insights.performance(FACTS, sort="nonsense")

        assert [r.sku for r in rows] == ["AAA", "BBB", "DDD", "CCC"]

    def test_pagination_reports_the_filtered_total_not_the_page(self) -> None:
        rows, total = insights.performance(FACTS, limit=2, offset=2)

        assert [r.sku for r in rows] == ["DDD", "CCC"]
        assert total == 4

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            (insights.Filters(search="bb"), ["BBB"]),
            (insights.Filters(search="BB"), ["BBB"]),
            (insights.Filters(min_sales=200), ["AAA", "BBB"]),
            (insights.Filters(max_sales=1), ["DDD", "CCC"]),
            (insights.Filters(min_sales=1, max_sales=200), ["BBB", "DDD"]),
            (insights.Filters(complaint_category=DAMAGE), ["DDD"]),
            (insights.Filters(min_qty=100), ["AAA", "CCC"]),
            (insights.Filters(max_qty=50), ["BBB", "DDD"]),
            (insights.Filters(status="critical"), ["BBB"]),
            (insights.Filters(status="attention"), ["DDD", "CCC"]),
            (insights.Filters(status="excellent"), ["AAA"]),
            # Combined, and matching nothing rather than falling back to all.
            (insights.Filters(status="critical", min_qty=100), []),
        ],
    )
    def test_filters(self, filters: insights.Filters, expected: list[str]) -> None:
        rows, total = insights.performance(FACTS, filters=filters)

        assert [r.sku for r in rows] == expected
        assert total == len(expected)

    def test_min_sales_pct_filters_on_the_share(self) -> None:
        rows, _ = insights.performance(FACTS, filters=insights.Filters(min_sales_pct=25))

        # Of the 701 units the sheet's SKUs sold: AAA 71.33%, BBB 28.53%.
        assert [r.sku for r in rows] == ["AAA", "BBB"]

    def test_the_column_sums_to_one_hundred(self) -> None:
        rows, _ = insights.performance(FACTS, limit=50)

        assert round(sum(r.shopify_sales_pct for r in rows), 2) == 100.0
        assert all(r.shopify_sales_pct <= 100 for r in rows)

    def test_status_is_computed_against_the_whole_workspace_not_the_filter(self) -> None:
        """Otherwise the goalposts move with every keystroke in the search box."""
        unfiltered = {row.sku: row.status for row in insights.performance(FACTS)[0]}

        narrowed, _ = insights.performance(FACTS, filters=insights.Filters(search="AAA"))

        assert narrowed[0].status == unfiltered["AAA"]

    def test_an_empty_workspace_is_an_empty_page(self) -> None:
        assert insights.performance([]) == ([], 0)

    def test_every_sortable_column_is_actually_sortable(self) -> None:
        """The router's pattern and this map must not drift apart."""
        for key in insights.SORTABLE:
            rows, _ = insights.performance(FACTS, sort=key)
            assert len(rows) == 4, key
