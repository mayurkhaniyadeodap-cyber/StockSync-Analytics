"""Request and response bodies for analytics.

Six cards and one table. The uploaded sheet is the source of truth for
everything except ``shopify_sales`` and ``shopify_sales_pct``, which Shopify
supplies and which are joined to a sheet row on the normalised SKU alone.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

#: The compact per-chart range control (design doc §7.3).
RANGE_PATTERN = "^(7|30|90|180|365)$"
#: The stock badge on a table row, derived from Quantity against the workspace
#: threshold. There is no Shopify inventory feed left to disagree with it.


class ComplaintScopePayload(BaseModel):
    """Whether the complaint figures on this page follow the selected range.

    Sent with every payload that carries a complaint number, because the number
    alone cannot say: "no complaints this month" and "complaints we cannot place
    in a month" are the same figure on screen.

    Counts, not copy. The sentence is the client's, built from these three in
    `ComplaintScopeNote`, where the numbers can be grouped for the locale.
    """

    #: True when at least some SKUs were imported with a Complaint Date column.
    filtered_by_date: bool = False
    dated_skus: int = 0
    undated_skus: int = 0
    #: Complaints no range can hold. The count is data, so a page can say how
    #: much of the tally it is not filtering.
    undated_complaints: int = 0


class KpiPayload(BaseModel):
    """The six dashboard cards, plus the context the percentage needs."""

    # From the uploaded sheet.
    total_skus: int
    total_quantity: int
    total_orders: int
    total_complaints: int
    #: Whether the figure above followed the range, and what to say if not.
    complaint_scope: ComplaintScopePayload = ComplaintScopePayload()

    # From Shopify, matched by SKU.
    shopify_sales: int
    #: Share of all Shopify units belonging to a SKU in the sheet. Bounded 0–100.
    shopify_sales_pct: float

    #: The denominator behind ``shopify_sales_pct`` — every unit the store sold in
    #: the window, matched or not. Returned so the card can be explained rather
    #: than taken on trust.
    shopify_sales_all: int
    revenue_paise: int
    low_stock: int
    #: Returned so the UI can say *why* a SKU counts as low without hard-coding a
    #: number that lives on the workspace.
    low_stock_threshold: int

    days: int
    #: True when orders exist that the rollup has not seen and nothing is
    #: working on it — which now means the automatic recompute failed.
    stale: bool
    #: True while a sync is queued or running. Mutually exclusive with `stale`
    #: by construction, so a page shows one message or the other and never both:
    #: figures being rebuilt are not figures that are behind.
    syncing: bool = False
    last_computed_at: datetime | None


class TrendPointPayload(BaseModel):
    day: date
    units: int
    revenue_paise: int


class TrendPayload(BaseModel):
    points: list[TrendPointPayload]
    #: The preceding window of equal length, for the comparison line.
    previous: list[TrendPointPayload]
    days: int


class ComplaintColumn(BaseModel):
    """One complaint category, so the client renders columns from the server."""

    field: str
    header: str


class SkuRowPayload(BaseModel):
    sku: str
    sku_normalized: str
    quantity: int
    shopify_sales: int
    shopify_sales_pct: float
    total_orders: int
    total_qty: int
    total_count: int
    #: Keyed by the same ``field`` values ``complaint_columns`` carries.
    complaints: dict[str, int]
    total_complaints: int
    stock_status: str


class SkuTablePage(BaseModel):
    rows: list[SkuRowPayload]
    #: The complaint columns, in sheet order. Sent with the page so the table's
    #: headers and its cells cannot disagree about the set.
    complaint_columns: list[ComplaintColumn]
    complaint_scope: ComplaintScopePayload = ComplaintScopePayload()
    total: int
    limit: int
    offset: int
    days: int


# ---------------------------------------------------------------------------
# Analytics page (business intelligence). The dashboard's payloads above stay
# as they are: the two pages answer different questions and must be free to
# change independently.
# ---------------------------------------------------------------------------

#: A SKU's automatic verdict, worst to best.
STATUS_PATTERN = "^(critical|attention|good|excellent)$"
#: Columns the performance table may be sorted by.
SORT_PATTERN = (
    "^(sku|total_count|total_qty|total_orders|shopify_sales|shopify_sales_pct"
    "|total_complaints|status)$"
)


class InsightKpiPayload(BaseModel):
    """The eight cards at the top of Analytics."""

    total_skus: int
    total_qty: int
    shopify_sales: int
    shopify_sales_pct: float
    total_orders: int
    total_complaints: int
    #: Units sold per SKU carried.
    avg_sales_per_sku: float
    shopify_sales_all: int


class RankedSkuPayload(BaseModel):
    rank: int
    sku: str
    sku_normalized: str
    shopify_sales: int
    shopify_sales_pct: float
    total_complaints: int
    total_qty: int
    total_orders: int


class NamedCountPayload(BaseModel):
    """One slice or bar. ``field_name`` is the complaint attribute or a SKU."""

    field_name: str
    label: str
    count: int
    share_pct: float


class SalesAnalyticsPayload(BaseModel):
    shopify_sales: int
    shopify_sales_pct: float
    #: Absent when nothing sold, rather than a zero row that reads as a finding.
    highest: RankedSkuPayload | None
    lowest: RankedSkuPayload | None
    top: list[RankedSkuPayload]
    distribution: list[NamedCountPayload]


class ComplaintAnalyticsPayload(BaseModel):
    total_complaints: int
    most_complained: RankedSkuPayload | None
    categories: list[NamedCountPayload]
    top_skus: list[RankedSkuPayload]
    skus_with_complaints: int


class RankingsPayload(BaseModel):
    top_selling: list[RankedSkuPayload]
    lowest_selling: list[RankedSkuPayload]
    highest_complaint: list[RankedSkuPayload]


class InventoryInsightsPayload(BaseModel):
    high_stock_low_sales: list[RankedSkuPayload]
    low_stock_high_sales: list[RankedSkuPayload]
    zero_sales: list[RankedSkuPayload]
    most_complaints: list[RankedSkuPayload]
    #: The cuts the lists were made at, so the UI states them rather than
    #: leaving "high" and "low" to the reader's imagination.
    median_qty: float
    median_sales: float
    zero_sales_total: int


class QuickInsightPayload(BaseModel):
    key: str
    #: Names an icon the client already has.
    icon: str
    title: str
    sku: str | None
    value: str
    note: str


class AnalyticsInsights(BaseModel):
    """Everything on Analytics except the performance table, in one request.

    One call rather than eight: the figures come from a single read of the SKU
    facts, and splitting them across requests would let two panels on the same
    screen describe two different moments.
    """

    kpis: InsightKpiPayload
    sales: SalesAnalyticsPayload
    complaints: ComplaintAnalyticsPayload
    rankings: RankingsPayload
    inventory: InventoryInsightsPayload
    quick: list[QuickInsightPayload]
    trend: TrendPayload
    complaint_columns: list[ComplaintColumn]
    complaint_scope: ComplaintScopePayload = ComplaintScopePayload()
    days: int
    has_data: bool
    #: True when orders exist that the rollup has not seen and nothing is
    #: working on it — which now means the automatic recompute failed.
    stale: bool
    #: True while a sync is queued or running. Mutually exclusive with `stale`
    #: by construction, so a page shows one message or the other and never both:
    #: figures being rebuilt are not figures that are behind.
    syncing: bool = False
    last_computed_at: datetime | None


class PerformanceRowPayload(BaseModel):
    sku: str
    sku_normalized: str
    total_count: int
    total_qty: int
    total_orders: int
    shopify_sales: int
    shopify_sales_pct: float
    total_complaints: int
    status: str
    complaints: dict[str, int]


class PerformancePage(BaseModel):
    rows: list[PerformanceRowPayload]
    complaint_columns: list[ComplaintColumn]
    complaint_scope: ComplaintScopePayload = ComplaintScopePayload()
    total: int
    limit: int
    offset: int
    days: int
    sort: str
    descending: bool


class RebuildResultPayload(BaseModel):
    rows_written: int
    days_covered: int
    duration_ms: int


class AnalyticsOverview(BaseModel):
    """Everything the dashboard needs above the table, in one request."""

    kpis: KpiPayload
    trend: TrendPayload
    has_data: bool
