"""Reading the analytics — plan §4.1, Layer 2.

Every figure the dashboard and the lenses show is a ``GROUP BY`` over
``sku_daily_metrics``, joined to ``inventory_items`` for stock. The rollup is
two orders of magnitude smaller than the raw line items, which is what makes
read-time aggregation viable at this store's volume.

**Open questions this module makes provisional choices about.** They are marked
at the line that decides, and none of them are settled:

* **Q1** — settled. A row states its quantity in three columns
  (``quantity_imported``, ``quantity_on_hand``, ``total_qty``), all written to
  the same value by the import. Everything that *displays* a quantity reads
  ``total_qty`` — see ``QUANTITY_FIELD`` — because it is the sheet's own column
  name and the one the importer treats as authoritative. The other two remain
  as raw storage.
* **Q2** — the seven KPI definitions. Revenue is units × unit price less the
  line discount; shipping and tax are order-level and excluded.
* **Q3** — "Out of stock" is ``total_qty = 0``. The stricter reading ("zero
  *and* still selling") is surfaced separately in Inventory Health rather than
  silently folded into the card.
* **C1** — the card is "Sell-through" (share of stock sold); the table column
  is "Sales %" (share of total units sold). Two different numbers, two names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core.calc import LIKE_ESCAPE, like_contains, normalise_search, sales_pct
from app.core.window import window_for
from app.models import (
    COMPLAINT_COLUMNS,
    InventoryItem,
    SkuDailyMetric,
    Workspace,
)
from app.repositories import complaints as complaints_repository
from app.repositories.complaints import ComplaintScope
from app.services.workspace_time import workspace_window

log = logging.getLogger(__name__)

#: The ranges the compact per-chart control offers (design doc §7.3).
#:
#: Presentation only — the API accepts any 1..365, which is deliberate so a
#: saved link or an export with a custom window keeps working. It is listed
#: here so the control and this module agree on what the buttons say.
OFFERED_RANGES = (7, 30, 90, 180, 365)
DEFAULT_RANGE = 30

#: **The quantity, everywhere.**
#:
#: A row states its quantity twice — ``quantity_on_hand`` and ``total_qty`` —
#: and the importer writes both from one cell, so they agree by construction.
#: They had drifted on rows written by an older parser, and because the
#: Dashboard summed the first while Analytics summed the second, one workspace
#: showed two different totals: 43,646 against 71,926.
#:
#: ``total_qty`` wins because it is the sheet's own column name, it is what the
#: UI already labels "Total Qty", and it is the value the importer treats as
#: authoritative when the two disagree. Legacy rows are reported by
#: ``python -m app.cli check-inventory`` rather than rewritten here.
QUANTITY_FIELD = InventoryItem.total_qty


def window(days: int, *, offset_minutes: int = 0) -> tuple[date, date]:
    """Inclusive [since, until] for a trailing window ending *today*.

    Kept as the module's public name — callers and tests reach for
    `analytics.window` — but the arithmetic lives in `core.window`, shared with
    `services.workspace_time`, so the rollup and the read layer cannot disagree
    about which days a range covers.
    """
    return window_for(days, offset_minutes=offset_minutes)


@dataclass
class Kpis:
    """The six cards. Four come from the sheet, two from Shopify."""

    #: From the sheet.
    total_skus: int = 0
    total_quantity: int = 0
    total_orders: int = 0
    #: For the selected range where the imported files carried complaint dates,
    #: and the sheet's own totals where they did not. `complaint_scope` says
    #: which, and carries the sentence the page shows when it is the latter.
    total_complaints: int = 0
    complaint_scope: ComplaintScope = field(default_factory=ComplaintScope)

    #: From Shopify, matched to the sheet by SKU.
    shopify_sales: int = 0
    #: Share of *all* Shopify units that belong to a SKU in the sheet — i.e. how
    #: much of the store's sales the sheet accounts for. Bounded 0–100, unlike a
    #: sales-over-stock ratio, which reads 8,512% on this store.
    shopify_sales_pct: float = 0.0

    #: Context, not cards: the whole store's figure, so the percentage above has
    #: a visible denominator.
    shopify_sales_all: int = 0
    revenue_paise: int = 0
    low_stock: int = 0
    low_stock_threshold: int = 10


@dataclass
class TrendPoint:
    day: date
    units: int
    revenue_paise: int


@dataclass
class Trend:
    points: list[TrendPoint] = field(default_factory=list)
    previous: list[TrendPoint] = field(default_factory=list)


@dataclass
class SkuRow:
    """One row of the main table: the sheet's columns plus Shopify's two."""

    sku: str
    sku_normalized: str
    quantity: int
    shopify_sales: int
    #: This SKU's share of what the sheet's SKUs sold in the window — the column
    #: is a composition, so it sums to 100% and no row exceeds it. The card
    #: above answers a different question and divides by the store's own total.
    shopify_sales_pct: float
    total_orders: int
    total_qty: int
    total_count: int
    #: Complaint categories keyed by model attribute, in COMPLAINT_COLUMNS order.
    complaints: dict[str, int]
    total_complaints: int
    stock_status: str


def _threshold(db: Session, workspace_id: int) -> int:
    """Workspace-level, not per-user: it changes the Low stock KPI everyone sees."""
    value = db.scalar(select(Workspace.low_stock_threshold).where(Workspace.id == workspace_id))
    return int(value or 10)


def kpis(db: Session, *, workspace_id: int, days: int = DEFAULT_RANGE) -> Kpis:
    """The six cards. Sheet figures and Shopify sales, matched by SKU."""
    since, until = workspace_window(db, workspace_id, days)
    threshold = _threshold(db, workspace_id)

    sheet = db.execute(
        select(
            func.count(InventoryItem.id),
            # `total_qty` is the quantity everywhere — see QUANTITY_FIELD. The
            # sum of `quantity_on_hand` was the old reading and disagreed with
            # Analytics by 28,280 units on rows written before the two were
            # reconciled at import time.
            func.coalesce(func.sum(QUANTITY_FIELD), 0),
            func.coalesce(func.sum(InventoryItem.total_orders), 0),
            func.sum(
                case(
                    (and_(QUANTITY_FIELD > 0, QUANTITY_FIELD <= threshold), 1),
                    else_=0,
                )
            ),
        ).where(InventoryItem.workspace_id == workspace_id)
    ).one()

    # The whole store's sales, and the part of it that matched a sheet SKU. Two
    # queries rather than one so the percentage has an explicit denominator.
    all_units = _window_units(db, workspace_id, since, until)
    matched_units, matched_revenue = _matched(db, workspace_id, since, until)

    # Complaints follow the range for the SKUs imported with dates on them and
    # are the sheet's totals for the rest — one rule, in one place, shared with
    # the table below the card. `scope` is what tells the page which it is.
    complaints = complaints_repository.resolve(db, workspace_id, since=since, until=until)

    return Kpis(
        total_skus=int(sheet[0] or 0),
        total_quantity=int(sheet[1] or 0),
        total_orders=int(sheet[2] or 0),
        total_complaints=complaints.total,
        complaint_scope=complaints.scope,
        shopify_sales=matched_units,
        shopify_sales_pct=sales_pct(matched_units, all_units),
        # The denominator, stated so the percentage can be checked rather than
        # taken on trust — the same reason the card shows it on screen.
        shopify_sales_all=all_units,
        revenue_paise=matched_revenue,
        low_stock=int(sheet[3] or 0),
        low_stock_threshold=threshold,
    )


def trend(db: Session, *, workspace_id: int, days: int = DEFAULT_RANGE) -> Trend:
    """Daily units and revenue, plus the preceding window for comparison.

    The prior period is returned alongside rather than fetched separately so
    the two lines on the chart can never describe different ranges.
    """
    since, until = workspace_window(db, workspace_id, days)
    previous_since = since - timedelta(days=days)
    previous_until = since - timedelta(days=1)

    def series(start: date, end: date) -> list[TrendPoint]:
        rows = db.execute(
            select(
                SkuDailyMetric.metric_date,
                func.sum(SkuDailyMetric.units_sold),
                func.sum(SkuDailyMetric.revenue_paise),
            )
            .where(
                SkuDailyMetric.workspace_id == workspace_id,
                SkuDailyMetric.metric_date >= start,
                SkuDailyMetric.metric_date <= end,
            )
            .group_by(SkuDailyMetric.metric_date)
            .order_by(SkuDailyMetric.metric_date)
        ).all()
        found = {
            (r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0]))): (
                int(r[1] or 0),
                int(r[2] or 0),
            )
            for r in rows
        }
        # Days with no sales are zeroes, not gaps — a line chart that skips
        # them draws a slope through the missing days and overstates activity.
        out: list[TrendPoint] = []
        cursor = start
        while cursor <= end:
            units, revenue = found.get(cursor, (0, 0))
            out.append(TrendPoint(day=cursor, units=units, revenue_paise=revenue))
            cursor += timedelta(days=1)
        return out

    return Trend(
        points=series(since, until),
        previous=series(previous_since, previous_until),
    )


def sku_table(
    db: Session,
    *,
    workspace_id: int,
    days: int = DEFAULT_RANGE,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SkuRow], int, ComplaintScope]:
    """The main table: every sheet SKU with its Shopify sales beside it.

    One left join on ``sku_normalized``. A SKU with no Shopify sales shows zero
    rather than being dropped — the sheet is the source of truth, so its rows
    exist whether or not the store sold any.
    """
    since, until = workspace_window(db, workspace_id, days)
    threshold = _threshold(db, workspace_id)

    units = func.coalesce(func.sum(SkuDailyMetric.units_sold), 0)

    stmt = (
        select(InventoryItem, units.label("units"))
        .outerjoin(
            SkuDailyMetric,
            and_(
                SkuDailyMetric.workspace_id == InventoryItem.workspace_id,
                SkuDailyMetric.sku_normalized == InventoryItem.sku_normalized,
                SkuDailyMetric.metric_date >= since,
                SkuDailyMetric.metric_date <= until,
            ),
        )
        .where(InventoryItem.workspace_id == workspace_id)
        .group_by(InventoryItem.id)
    )

    # `%` and `_` are LIKE metacharacters. Interpolated raw they turned the
    # search box into a pattern language on this screen and left it a literal
    # substring match on SKU Performance — the same term, two answers. Both now
    # go through `calc`.
    term = normalise_search(search)
    if term:
        stmt = stmt.where(
            func.lower(InventoryItem.sku).like(like_contains(term), escape=LIKE_ESCAPE)
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # The imported SKUs' own sales, not the store's. This is the same table the
    # SKU Performance page draws, so the column has to be the same figure — see
    # `insights.performance`. It is the unfiltered total on purpose: searching
    # for one SKU must not make it 100% of itself.
    imported_units, _ = _matched(db, workspace_id, since, until)

    rows = db.execute(
        # Highest sales first; ties alphabetically by the SKU as written.
        # `sku_normalized` was the old tiebreak and is not alphabetical by the
        # displayed code — it drops separators, so `DD-10` and `DD-2` order by
        # `dd10`/`dd2` rather than by what the table shows. Lowercased so case
        # does not split the alphabet into two runs.
        stmt.order_by(units.desc(), func.lower(InventoryItem.sku)).limit(limit).offset(offset)
    ).all()

    # The same decision the Analytics page makes, from the same helper: a SKU
    # imported with complaint dates reports the window, one without reports the
    # sheet's totals. Reading it here rather than off `item` is what keeps this
    # table and SKU Performance showing one number per SKU.
    resolved = complaints_repository.resolve(db, workspace_id, since=since, until=until)

    result: list[SkuRow] = []
    for item, sold in rows:
        sold = int(sold or 0)
        by_category = resolved.by_sku.get(
            item.sku_normalized,
            {attribute: 0 for attribute, _ in COMPLAINT_COLUMNS},
        )
        # QUANTITY_FIELD: the table column, the stock badge and the KPI card
        # above it all read the sheet's Total Qty, so they cannot disagree.
        quantity = item.total_qty
        result.append(
            SkuRow(
                sku=item.sku,
                sku_normalized=item.sku_normalized,
                quantity=quantity,
                shopify_sales=sold,
                shopify_sales_pct=sales_pct(sold, imported_units),
                total_orders=item.total_orders,
                total_qty=item.total_qty,
                total_count=item.total_count,
                complaints=by_category,
                total_complaints=sum(by_category.values()),
                stock_status=("out" if quantity <= 0 else "low" if quantity <= threshold else "in"),
            )
        )
    return result, total, resolved.scope


def _matched(db: Session, workspace_id: int, since: date, until: date) -> tuple[int, int]:
    """Units and revenue for the window, limited to SKUs the sheet carries.

    The units half is the denominator of the SKU table's Shopify Sales %, which
    is a share of the imported SKUs' own sales; the KPI card divides the same
    numerator by ``_window_units`` instead. See ``calc.sales_pct``.
    """
    row = db.execute(
        select(
            func.coalesce(func.sum(SkuDailyMetric.units_sold), 0),
            func.coalesce(func.sum(SkuDailyMetric.revenue_paise), 0),
        )
        .join(
            InventoryItem,
            and_(
                InventoryItem.workspace_id == SkuDailyMetric.workspace_id,
                # SKU only. There is no variant, no product id, nothing else to
                # match on — which is the whole point of the simplification.
                InventoryItem.sku_normalized == SkuDailyMetric.sku_normalized,
            ),
        )
        .where(
            SkuDailyMetric.workspace_id == workspace_id,
            SkuDailyMetric.metric_date >= since,
            SkuDailyMetric.metric_date <= until,
        )
    ).one()
    return int(row[0]), int(row[1])


def _window_units(db: Session, workspace_id: int, since: date, until: date) -> int:
    """Every Shopify unit in the window — the denominator for the KPI card."""
    return int(
        db.scalar(
            select(func.coalesce(func.sum(SkuDailyMetric.units_sold), 0)).where(
                SkuDailyMetric.workspace_id == workspace_id,
                SkuDailyMetric.metric_date >= since,
                SkuDailyMetric.metric_date <= until,
            )
        )
        or 0
    )
