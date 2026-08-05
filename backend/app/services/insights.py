"""Business intelligence over the imported sheet and Shopify's units sold.

Every function here is a pure derivation from ``list[SkuFact]``: no session, no
query, no clock. That is what makes the page testable against a handful of
literal rows instead of a seeded database, and it is why the eight KPI cards and
the table below them can be trusted to agree — they fold the same list.

**Thresholds are relative, not magic.** "High stock", "low sales" and the rest
are decided against the *median* of this workspace's own SKUs rather than against
a constant. A hard-coded "sales < 10 is low" is wrong for every store except the
one it was written for; a median re-scales itself and needs no configuration.
The medians are returned alongside the findings so the UI can say what the cut
actually was.

Nothing here invents a row. Where a store has no SKUs, no orders or no sales,
the answer is an empty list or a zero — never a placeholder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Literal

from app.core.calc import format_pct, matches_search, sales_pct, share_pct
from app.models import COMPLAINT_COLUMNS
from app.repositories.analytics import SkuFact
from app.repositories.complaints import ComplaintScope, scope_from

# The report writers' own table type. Imported, not redefined: the CSV and XLSX
# writers already neutralise formula-like cells, and an export that rebuilt that
# by hand would be the one place formula injection came back.
from app.services.report_tables import Column, ReportTable

log = logging.getLogger(__name__)

#: How many rows a ranking or chart returns. Enough to read at a glance; the
#: full set is what the performance table is for.
TOP_N = 10

#: A SKU's overall verdict. Ordered worst to best so a sort is meaningful.
Status = Literal["critical", "attention", "good", "excellent"]

#: How many complaints put a SKU past merely worth watching.
#:
#: Counts, not a rate. Complaint Rate % is no longer computed anywhere, so a
#: threshold expressed as a percentage would have to derive one — and an
#: absolute count is what the status filter has left to work with.
#:
#: The trade-off is real and worth stating: a count does not scale, so a
#: high-volume SKU reaches these lines on a smaller share of its stock than a
#: small one does. If status needs to discriminate better than this, it needs a
#: measure of its own rather than a borrowed one.
CRITICAL_COMPLAINTS = 25
ATTENTION_COMPLAINTS = 8
#: At or below this a SKU can qualify as excellent, given it also sells.
EXCELLENT_COMPLAINTS = 2


@dataclass
class Kpis:
    """The eight cards at the top of the page."""

    total_skus: int = 0
    total_qty: int = 0
    shopify_sales: int = 0
    #: Share of *all* Shopify units that belong to a SKU in the sheet. Bounded
    #: 0–100, unlike sales-over-stock, which reads in the thousands here.
    shopify_sales_pct: float = 0.0
    total_orders: int = 0
    total_complaints: int = 0
    #: Units sold divided by SKUs carried. Says whether sales are broad or
    #: concentrated once read against the top-sellers chart.
    avg_sales_per_sku: float = 0.0
    #: The denominator behind ``shopify_sales_pct``: every unit the store sold
    #: in the window, matched to the sheet or not. Stated so the percentage can
    #: be checked rather than taken on trust.
    shopify_sales_all: int = 0


@dataclass
class RankedSku:
    """One row of a ranking table or one bar of a chart."""

    rank: int
    sku: str
    sku_normalized: str
    shopify_sales: int = 0
    shopify_sales_pct: float = 0.0
    total_complaints: int = 0
    total_qty: int = 0
    total_orders: int = 0


@dataclass
class NamedCount:
    """A complaint category and how many of it there were."""

    field_name: str
    label: str
    count: int
    share_pct: float = 0.0


@dataclass
class SalesAnalytics:
    shopify_sales: int = 0
    shopify_sales_pct: float = 0.0
    highest: RankedSku | None = None
    lowest: RankedSku | None = None
    top: list[RankedSku] = field(default_factory=list)
    #: Top sellers plus an explicit "Other" remainder, so the slices sum to the
    #: whole and the chart is not quietly showing a subset as if it were one.
    distribution: list[NamedCount] = field(default_factory=list)


@dataclass
class ComplaintAnalytics:
    total_complaints: int = 0
    most_complained: RankedSku | None = None
    #: All ten categories, largest first, zeroes included — an absent category is
    #: itself information.
    categories: list[NamedCount] = field(default_factory=list)
    top_skus: list[RankedSku] = field(default_factory=list)
    #: How the complaints are spread across SKUs: how many carry any at all.
    skus_with_complaints: int = 0


@dataclass
class Rankings:
    top_selling: list[RankedSku] = field(default_factory=list)
    lowest_selling: list[RankedSku] = field(default_factory=list)
    highest_complaint: list[RankedSku] = field(default_factory=list)


@dataclass
class InventoryInsights:
    high_stock_low_sales: list[RankedSku] = field(default_factory=list)
    low_stock_high_sales: list[RankedSku] = field(default_factory=list)
    zero_sales: list[RankedSku] = field(default_factory=list)
    most_complaints: list[RankedSku] = field(default_factory=list)
    #: The cuts the four lists above were made at, so the UI states them instead
    #: of leaving the reader to guess what "high" meant.
    median_qty: float = 0.0
    median_sales: float = 0.0
    zero_sales_total: int = 0


@dataclass
class QuickInsight:
    """One generated card. ``sku`` is None only for portfolio-wide findings.

    ``icon`` names an icon the client already has rather than describing one, so
    a new insight cannot ship a card with a blank square where a glyph should be.
    """

    key: str
    icon: str
    title: str
    sku: str | None
    value: str
    note: str


@dataclass
class PerformanceRow:
    sku: str
    sku_normalized: str
    #: The sheet's own "Total Count" column, shown beside Total Qty on the
    #: performance table so the two figures can be compared row by row.
    total_count: int
    total_qty: int
    total_orders: int
    shopify_sales: int
    shopify_sales_pct: float
    #: For the window when this SKU was imported with complaint dates, and
    #: the sheet's whole tally when it was not. See `repositories.complaints`.
    total_complaints: int
    status: Status
    complaints: dict[str, int] = field(default_factory=dict)


@dataclass
class Filters:
    """The performance table's filters. All optional; all combine with AND."""

    search: str | None = None
    min_sales: int | None = None
    max_sales: int | None = None
    complaint_category: str | None = None
    min_sales_pct: float | None = None
    min_qty: int | None = None
    max_qty: int | None = None
    status: Status | None = None


#: Columns the table may be sorted by, mapped to the row attribute. A whitelist
#: rather than free-form: the sort key arrives from a query string.
SORTABLE: dict[str, str] = {
    # The SKU *as displayed*, not the normalised key. `sku_normalized` drops
    # separators, so it sorts `0381b_velvet` before `0381_velvet` while the
    # Dashboard — which sorts on `lower(sku)` — puts them the other way round.
    # Same rows, two alphabets. See `services.analytics.sku_table`.
    "sku": "sku",
    "total_count": "total_count",
    "total_qty": "total_qty",
    "total_orders": "total_orders",
    "shopify_sales": "shopify_sales",
    "shopify_sales_pct": "shopify_sales_pct",
    "total_complaints": "total_complaints",
    "status": "status",
}

#: How the SKU table is ordered wherever it appears — the Dashboard, the SKU
#: Performance page and the Reports centre export.
#:
#: One definition because "the same sorting" is a business rule, not a default
#: three call sites happen to agree on. Worst first: the table exists to surface
#: what needs attention, and a table sorted by sales buries it.
DEFAULT_SORT = "total_complaints"
DEFAULT_DESCENDING = True

#: Worst first, so sorting by status ascending surfaces what needs attention.
_STATUS_ORDER: dict[str, int] = {"critical": 0, "attention": 1, "good": 2, "excellent": 3}


#: The shared implementation. Kept under its old private name so the many call
#: sites below read unchanged, but there is now one definition of a percentage.
_share = share_pct


def complaint_scope(facts: list[SkuFact]) -> ComplaintScope:
    """Whether the complaint figures derived from ``facts`` follow the range.

    Every fact already knows whether its SKU was imported with dates and how
    many of its complaints no window can hold, so this is a fold rather than a
    second read — and it goes through ``scope_from``, the same counting the
    Dashboard's table uses, so the two screens cannot disagree about the note.
    """
    return scope_from((f.complaints_are_dated, f.unfilterable_complaints) for f in facts)


def matched_units(facts: list[SkuFact]) -> int:
    """Units sold in the selected window, across the imported SKUs.

    Two jobs, which is why it is one function. It is the *numerator* of the KPI
    card's Shopify Sales % — the denominator there is the store's own total,
    which these facts cannot see — and the *denominator* of the SKU table's,
    where the column is a share of the sheet rather than of the store.
    """
    return sum(fact.shopify_sales for fact in facts)


def status_for(fact: SkuFact, *, median_sales: float) -> Status:
    """A SKU's verdict, from its complaint count and whether it moves.

    Read top to bottom; the first match wins. Complaints dominate because a
    defective SKU that sells well is a problem that sells well.

    Counts rather than a rate: Complaint Rate % was removed from the project,
    and this classifier is not a place to keep computing one privately.
    """
    if fact.total_complaints >= CRITICAL_COMPLAINTS:
        return "critical"
    if fact.total_complaints >= ATTENTION_COMPLAINTS:
        return "attention"

    # Stock sitting still. Not "critical" — nothing is going wrong for a
    # customer — but it is the other thing a buyer needs to see.
    if fact.quantity > 0 and fact.shopify_sales == 0:
        return "attention"

    if (
        fact.shopify_sales >= median_sales
        and fact.shopify_sales > 0
        and fact.total_complaints <= EXCELLENT_COMPLAINTS
    ):
        return "excellent"
    return "good"


def kpis(facts: list[SkuFact], *, all_units: int) -> Kpis:
    """The eight cards. One pass over the list."""
    if not facts:
        return Kpis(shopify_sales_all=all_units)

    total_qty = sum(f.total_qty for f in facts)
    sales = matched_units(facts)
    orders = sum(f.total_orders for f in facts)
    complaints = sum(f.total_complaints for f in facts)

    return Kpis(
        total_skus=len(facts),
        total_qty=total_qty,
        shopify_sales=sales,
        shopify_sales_pct=sales_pct(sales, all_units),
        total_orders=orders,
        total_complaints=complaints,
        avg_sales_per_sku=round(sales / len(facts), 1),
        # The window on both sides, like the column: `complaints` is already
        # scoped to it by the repository, and `sales` is the same window's.
        shopify_sales_all=all_units,
    )


def _ranked(facts: list[SkuFact], *, all_units: int, start: int = 1) -> list[RankedSku]:
    """Turn facts into ranked rows, numbering from ``start``.

    ``all_units`` is every unit the store sold in the window — the Shopify
    Sales % denominator, threaded through unchanged by every caller.
    """
    return [
        RankedSku(
            rank=index,
            sku=fact.sku,
            sku_normalized=fact.sku_normalized,
            shopify_sales=fact.shopify_sales,
            shopify_sales_pct=sales_pct(fact.shopify_sales, all_units),
            total_complaints=fact.total_complaints,
            total_qty=fact.total_qty,
            total_orders=fact.total_orders,
        )
        for index, fact in enumerate(facts, start=start)
    ]


def _by_sales(facts: list[SkuFact], *, descending: bool) -> list[SkuFact]:
    # Equal sales rank alphabetically by the SKU as written, lowercased so case
    # does not split the alphabet in two. Deterministic, and it matches the
    # order the dashboard's own query produces.
    return sorted(
        facts,
        key=lambda f: (-f.shopify_sales if descending else f.shopify_sales, f.sku.lower()),
    )


def sales(facts: list[SkuFact], *, all_units: int) -> SalesAnalytics:
    """Sales, its extremes, and how concentrated it is."""
    if not facts:
        return SalesAnalytics()

    total = sum(f.shopify_sales for f in facts)
    ordered = _by_sales(facts, descending=True)
    top = _ranked(ordered[:TOP_N], all_units=all_units)

    # The lowest seller is the *last* by sales, ranked as itself rather than as
    # position 1 of a reversed list, which would read as "the best".
    lowest_fact = ordered[-1]
    lowest = _ranked([lowest_fact], all_units=all_units, start=len(ordered))[0]

    named = sum(row.shopify_sales for row in top)
    distribution = [
        NamedCount(
            field_name=row.sku_normalized,
            label=row.sku,
            count=row.shopify_sales,
            share_pct=_share(row.shopify_sales, total),
        )
        for row in top
        if row.shopify_sales > 0
    ]
    remainder = total - named
    if remainder > 0:
        distribution.append(
            NamedCount(
                field_name="__other__",
                label=f"Other ({len(ordered) - len(top)} SKUs)",
                count=remainder,
                share_pct=_share(remainder, total),
            )
        )

    return SalesAnalytics(
        shopify_sales=total,
        shopify_sales_pct=sales_pct(total, all_units),
        highest=top[0] if top and top[0].shopify_sales > 0 else None,
        lowest=lowest,
        top=[row for row in top if row.shopify_sales > 0],
        distribution=distribution,
    )


def complaints(facts: list[SkuFact], *, all_units: int) -> ComplaintAnalytics:
    """The ten categories, and which SKUs carry them."""
    if not facts:
        return ComplaintAnalytics()

    total = sum(f.total_complaints for f in facts)

    per_category = {attribute: 0 for attribute, _ in COMPLAINT_COLUMNS}
    for fact in facts:
        for attribute in per_category:
            per_category[attribute] += fact.complaints.get(attribute, 0)

    categories = sorted(
        (
            NamedCount(
                field_name=attribute,
                label=header,
                count=per_category[attribute],
                share_pct=_share(per_category[attribute], total),
            )
            for attribute, header in COMPLAINT_COLUMNS
        ),
        key=lambda c: (-c.count, c.label),
    )

    with_complaints = [f for f in facts if f.total_complaints > 0]
    worst = sorted(with_complaints, key=lambda f: (-f.total_complaints, f.sku_normalized))
    top_skus = _ranked(worst[:TOP_N], all_units=all_units)

    return ComplaintAnalytics(
        total_complaints=total,
        most_complained=top_skus[0] if top_skus else None,
        categories=categories,
        top_skus=top_skus,
        skus_with_complaints=len(with_complaints),
    )


def rankings(facts: list[SkuFact], *, all_units: int) -> Rankings:
    """The three ranking tables."""
    if not facts:
        return Rankings()

    ordered = _by_sales(facts, descending=True)
    # Lowest sellers are the tail, ranked 1..N as "worst first" — the table is
    # read as a leaderboard of its own, not as the bottom of the other one.
    tail = _by_sales(facts, descending=False)[:TOP_N]

    complained = [f for f in facts if f.total_complaints > 0]
    worst = sorted(complained, key=lambda f: (-f.total_complaints, f.sku_normalized))

    return Rankings(
        top_selling=[
            r for r in _ranked(ordered[:TOP_N], all_units=all_units) if r.shopify_sales > 0
        ],
        lowest_selling=_ranked(tail, all_units=all_units),
        highest_complaint=_ranked(worst[:TOP_N], all_units=all_units),
    )


def inventory_insights(facts: list[SkuFact], *, all_units: int) -> InventoryInsights:
    """Stock read against sales, cut at this workspace's own medians."""
    if not facts:
        return InventoryInsights()

    quantities = [float(f.total_qty) for f in facts]
    sales_values = [float(f.shopify_sales) for f in facts]
    median_qty = float(median(quantities))
    median_sales = float(median(sales_values))

    overstocked = sorted(
        (f for f in facts if f.total_qty > median_qty and f.shopify_sales <= median_sales),
        key=lambda f: (-f.total_qty, f.sku_normalized),
    )
    understocked = sorted(
        (f for f in facts if f.total_qty <= median_qty and f.shopify_sales > median_sales),
        key=lambda f: (-f.shopify_sales, f.sku_normalized),
    )
    # Stock on the shelf that sold nothing. Excludes SKUs with no stock, where
    # zero sales is expected rather than a finding.
    dead = sorted(
        (f for f in facts if f.total_qty > 0 and f.shopify_sales == 0),
        key=lambda f: (-f.total_qty, f.sku_normalized),
    )
    complained = sorted(
        (f for f in facts if f.total_complaints > 0),
        key=lambda f: (-f.total_complaints, f.sku_normalized),
    )

    return InventoryInsights(
        high_stock_low_sales=_ranked(overstocked[:TOP_N], all_units=all_units),
        low_stock_high_sales=_ranked(understocked[:TOP_N], all_units=all_units),
        zero_sales=_ranked(dead[:TOP_N], all_units=all_units),
        most_complaints=_ranked(complained[:TOP_N], all_units=all_units),
        median_qty=median_qty,
        median_sales=median_sales,
        zero_sales_total=len(dead),
    )


def quick_insights(facts: list[SkuFact], *, all_units: int) -> list[QuickInsight]:
    """One card per finding that the data actually supports.

    A finding with nothing behind it is omitted rather than shown empty: a card
    reading "Best performing: —" tells the reader nothing and looks broken.
    """
    if not facts:
        return []

    found: list[QuickInsight] = []
    median_sales = float(median([float(f.shopify_sales) for f in facts]))

    def add(key: str, icon: str, title: str, fact: SkuFact, value: str, note: str) -> None:
        found.append(
            QuickInsight(key=key, icon=icon, title=title, sku=fact.sku, value=value, note=note)
        )

    selling = [f for f in facts if f.shopify_sales > 0]

    # Best performing: sells most among those with a clean complaint record. A
    # top seller that generates complaints is the *next* card, not this one.
    clean = [f for f in selling if f.total_complaints == 0]
    if clean:
        best = max(clean, key=lambda f: (f.shopify_sales, f.sku_normalized))
        add(
            "best",
            "check",
            "Best performing SKU",
            best,
            f"{best.shopify_sales:,} units",
            "highest sales with no complaints logged",
        )

    worst = [f for f in facts if f.total_complaints > 0]
    if worst:
        top = max(worst, key=lambda f: (f.total_complaints, f.sku_normalized))
        add(
            "complaints",
            "warn",
            "Highest complaint SKU",
            top,
            f"{top.total_complaints:,} complaints",
            f"against {top.total_qty:,} units of stock"
            if top.total_qty
            else "no quantity on the sheet",
        )

    # Fastest selling is turnover, not volume: units sold against units held. A
    # SKU that sold its whole shelf outranks one that sold more from a bigger one.
    turning = [f for f in selling if f.total_qty > 0]
    if turning:
        fastest = max(turning, key=lambda f: (f.shopify_sales / f.total_qty, f.sku_normalized))
        add(
            "fastest",
            "chart",
            "Fastest selling SKU",
            fastest,
            f"{round(fastest.shopify_sales / fastest.total_qty, 1)}× stock",
            f"{fastest.shopify_sales:,} sold against {fastest.total_qty:,} held",
        )

    idle = [f for f in facts if f.total_qty > 0 and f.shopify_sales <= median_sales]
    if idle:
        over = max(idle, key=lambda f: (f.total_qty, f.sku_normalized))
        add(
            "overstocked",
            "box",
            "Overstocked SKU",
            over,
            f"{over.total_qty:,} units held",
            f"only {over.shopify_sales:,} sold",
        )

    # Restock: selling above the median while holding less stock than it sold.
    thin = [f for f in selling if f.shopify_sales > median_sales and f.total_qty < f.shopify_sales]
    if thin:
        restock = max(thin, key=lambda f: (f.shopify_sales - f.total_qty, f.sku_normalized))
        add(
            "restock",
            "bell",
            "Restock needed",
            restock,
            f"{restock.total_qty:,} left",
            f"{restock.shopify_sales:,} sold in the window",
        )

    dead = [f for f in facts if f.total_qty > 0 and f.shopify_sales == 0]
    if dead:
        worst_dead = max(dead, key=lambda f: (f.total_qty, f.sku_normalized))
        found.append(
            QuickInsight(
                key="nosales",
                icon="x",
                title="No sales",
                sku=worst_dead.sku,
                value=f"{len(dead):,} SKUs",
                note=f"holding stock but unsold — {worst_dead.sku} has the most at "
                f"{worst_dead.total_qty:,}",
            )
        )

    return found


def performance(
    facts: list[SkuFact],
    *,
    filters: Filters | None = None,
    sort: str = "shopify_sales",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PerformanceRow], int]:
    """The searchable, sortable table. Returns the page and the filtered total.

    Filtering happens after the status is computed, because status is one of the
    things you can filter on — and it depends on the median of the *whole*
    workspace, not of whatever survived the filter. Narrowing the set first would
    move the goalposts with every keystroke.

    **Shopify Sales % here is a share of the imported SKUs' sales**, not of the
    store's — this table is a composition of one whole, so the column sums to
    100% and no row can exceed it. The KPI cards above it answer the other
    question and keep the store denominator; see ``calc.sales_pct``.

    The denominator is derived from ``facts`` rather than passed in, so no
    caller can hand this table the wrong one. It is the unfiltered total on
    purpose: filtering to one SKU must not make it 100% of itself.
    """
    if not facts:
        return [], 0

    imported_sales = matched_units(facts)
    median_sales = float(median([float(f.shopify_sales) for f in facts]))
    rows = [
        PerformanceRow(
            sku=fact.sku,
            sku_normalized=fact.sku_normalized,
            total_count=fact.total_count,
            total_qty=fact.total_qty,
            total_orders=fact.total_orders,
            shopify_sales=fact.shopify_sales,
            shopify_sales_pct=sales_pct(fact.shopify_sales, imported_sales),
            total_complaints=fact.total_complaints,
            status=status_for(fact, median_sales=median_sales),
            complaints=dict(fact.complaints),
        )
        for fact in facts
    ]

    if filters is not None:
        rows = [row for row in rows if _matches(row, filters)]

    key = SORTABLE.get(sort, "shopify_sales")
    # Two passes, exploiting that Python's sort is stable. The tiebreak cannot be
    # part of the sort key: `reverse=True` would reverse it along with the
    # primary, so equal sales would come back Z-to-A descending and A-to-Z
    # ascending. Sorting by the tiebreak first and then stably by the primary
    # keeps SKUs alphabetical within a tie in *both* directions.
    rows.sort(key=lambda row: row.sku_normalized)
    rows.sort(key=lambda row: _sort_key(row, key), reverse=descending)

    return rows[offset : offset + limit], len(rows)


def _sort_key(row: PerformanceRow, key: str) -> float | int | str:
    if key == "status":
        return _STATUS_ORDER[row.status]
    if key == "sku":
        # Lowercased so case does not split the alphabet into two runs, exactly
        # as the Dashboard's `func.lower(InventoryItem.sku)` does.
        return row.sku.lower()
    value = getattr(row, key)
    assert isinstance(value, (int, float, str))  # noqa: S101 - SORTABLE is a whitelist
    return value


def _matches(row: PerformanceRow, f: Filters) -> bool:
    # Same question as the SKU table's SQL LIKE, same answer — both fold the
    # term through `calc` so a metacharacter cannot mean one thing here and
    # another there.
    if f.search and not matches_search(row.sku, f.search):
        return False
    if f.min_sales is not None and row.shopify_sales < f.min_sales:
        return False
    if f.max_sales is not None and row.shopify_sales > f.max_sales:
        return False
    if f.complaint_category and row.complaints.get(f.complaint_category, 0) <= 0:
        return False
    if f.min_sales_pct is not None and row.shopify_sales_pct < f.min_sales_pct:
        return False
    if f.min_qty is not None and row.total_qty < f.min_qty:
        return False
    if f.max_qty is not None and row.total_qty > f.max_qty:
        return False
    if f.status is not None and row.status != f.status:
        return False
    return True


#: The performance table as an export, column for column **and in the same
#: order the screen shows them**, so a download can never disagree with what was
#: on it — including which column is where.
#:
#: The seven summary columns are the page's; the ten categories follow from
#: ``COMPLAINT_COLUMNS`` in the reading order the page uses. ``Status`` is not
#: here: it is a derived badge the table no longer shows, and a column in the
#: file that is not on the screen is the same defect as one on the screen that
#: is not in the file.
EXPORT_SUMMARY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("SKU", "left"),
    ("Complaints", "right"),
    ("Shopify Sales", "right"),
    ("Shopify Sales %", "right"),
    ("Total Quantity", "right"),
    ("Total Orders", "right"),
)

#: The ten categories in the page's reading order: the four delivery faults,
#: then defect, damage and electronics as partial/complete pairs. Spelled here
#: rather than taken from ``COMPLAINT_COLUMNS`` because that tuple is in sheet
#: order and carries the sheet's own longer headings.
EXPORT_CATEGORY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("missing", "Missing"),
    ("missing_part", "Missing Part"),
    ("item_mismatch_wrong_item", "Wrong Item Delivered"),
    ("order_wrong_parcel", "Order Wrong Parcel"),
    ("item_defect_partial", "Item Defect Partial"),
    ("item_defect_complete", "Item Defect Complete"),
    ("item_damage_partial", "Item Damage Partial"),
    ("item_damage_complete", "Item Damage Complete"),
    ("electronics_nonworking_partial", "Electronics Nonworking Partial"),
    ("electronics_nonworking_complete", "Electronics Nonworking Complete"),
)

_EXPORT_COLUMNS: tuple[tuple[str, str], ...] = EXPORT_SUMMARY_COLUMNS + tuple(
    (header, "right") for _attribute, header in EXPORT_CATEGORY_COLUMNS
)


def as_report_table(
    rows: list[PerformanceRow], *, days: int, total: int, truncated: bool
) -> ReportTable:
    """Shape performance rows for the CSV and XLSX writers.

    Reuses ``report_files``' writers rather than formatting a download here: they
    already neutralise formula-like cells, which a hand-rolled CSV of user-supplied
    SKUs would not. This function only chooses columns and stringifies.

    Percentages are written as plain numbers with no ``%`` sign so a spreadsheet
    can average a column, and to two decimals through ``format_pct`` so the file
    and the screen it was taken from cannot round differently.

    The subtitle names the window for the Shopify figures only. Complaint
    counts are the sheet's own totals and do not move with it.
    """
    return ReportTable(
        title="SKU performance",
        subtitle=(
            f"{total:,} SKUs · Shopify sales over the last {days} days · "
            "complaints are the imported sheet's totals"
        ),
        columns=tuple(Column(header=header, align=align) for header, align in _EXPORT_COLUMNS),
        rows=[
            [
                row.sku,
                str(row.total_complaints),
                str(row.shopify_sales),
                format_pct(row.shopify_sales_pct),
                str(row.total_qty),
                str(row.total_orders),
                *(
                    str(row.complaints.get(attribute, 0))
                    for attribute, _header in EXPORT_CATEGORY_COLUMNS
                ),
            ]
            for row in rows
        ],
        truncated=truncated,
    )


#: Spelled for a person reading a spreadsheet, not for the wire.
