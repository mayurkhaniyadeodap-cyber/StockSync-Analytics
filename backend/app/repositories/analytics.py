"""Per-SKU facts, read once so every figure on the Analytics page agrees.

The page shows eight KPIs, three rankings, four insight lists, five charts and a
filterable table. Almost all of it is a different question about the *same* set
of rows: one per SKU in the sheet, with its Shopify units beside it.

So the rows are read once and derived from in Python, rather than each widget
issuing its own ``GROUP BY``. Two reasons, in order of importance:

1. **They cannot disagree.** "Total complaints" on a card and the sum of the
   complaint column in the table are the same addition over the same list, not
   two aggregates that drift apart the first time a filter or a join differs.
2. It is one round trip instead of fifteen.

The cost is holding the SKUs in memory. At this store that is ~1,600 rows of
about twenty small integers — tens of microseconds to fold, and nothing next to
the 429,000 order line items the rollup already absorbed. If the sheet grew by
two orders of magnitude this would need revisiting; the boundary is here, in one
repository, so that change would be local.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import COMPLAINT_COLUMNS, InventoryItem, SkuDailyMetric
from app.repositories import complaints as complaints_repository
from app.repositories.complaints import ComplaintScope


@dataclass(frozen=True)
class SkuFact:
    """One SKU: what the sheet says, and what Shopify sold.

    Deliberately flat and pre-summed. Every derivation downstream is arithmetic
    on these fields, so there is no query in the middle of a calculation.
    """

    sku: str
    sku_normalized: str
    #: Both read ``total_qty``. The importer writes the sheet's quantity to
    #: several columns from one cell, and every surface reads that one, so the
    #: Dashboard, the table and the insights cannot report different totals.
    #: Kept as two fields because the table shows one and the insights name the
    #: other; they are the same number by construction.
    quantity: int
    total_qty: int
    total_orders: int
    total_count: int
    #: Complaints by category — **for the selected window if this SKU was
    #: imported with dates, otherwise the sheet's own running totals.**
    #:
    #: Which one it is depends on the file the SKU arrived in, not on the
    #: workspace: the complaint export carries a date per row and the aggregated
    #: sheet carries none, and both are supported. ``complaints_are_dated`` says
    #: which answer this row is, and ``repositories.complaints`` owns the rule.
    complaints: dict[str, int] = field(default_factory=dict)
    #: The sum of the ten categories above.
    total_complaints: int = 0
    #: True when the counts above moved with the selected range.
    complaints_are_dated: bool = False
    #: Complaints on record for this SKU that no range can include — all of them
    #: when it is undated, or just the ones whose date cell was blank when it is
    #: not. Kept per SKU so the page can say how much of the tally a filtered
    #: view is not showing.
    unfilterable_complaints: int = 0
    #: From Shopify, matched on the normalised SKU alone. **Scoped to the
    #: selected window**, unlike the complaint columns.
    shopify_sales: int = 0
    revenue_paise: int = 0


class SkuFactRepository:
    """Reads. Computes nothing beyond what SQL sums for free."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def facts(self, workspace_id: int, *, since: date, until: date) -> list[SkuFact]:
        """Every sheet SKU, with Shopify units for the window joined on.

        A left join: a SKU the store never sold still has a row, because the
        sheet is the source of truth and "sold nothing" is the finding, not a
        reason to omit it.

        **Complaints follow the window for the SKUs that can.** Those imported
        from a complaint export carry a date per row and are summed over the
        range; those from an aggregated sheet have no date to sum over and keep
        the totals it stated. See ``repositories.complaints``, which owns that
        rule for this and for the Dashboard's own table alike.
        """
        units = func.coalesce(func.sum(SkuDailyMetric.units_sold), 0)
        revenue = func.coalesce(func.sum(SkuDailyMetric.revenue_paise), 0)

        rows = self._db.execute(
            select(InventoryItem, units.label("units"), revenue.label("revenue"))
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
        ).all()

        window = complaints_repository.read(self._db, workspace_id, since=since, until=until)

        facts: list[SkuFact] = []
        for item, sold, revenue_paise in rows:
            totals = {
                attribute: int(getattr(item, attribute) or 0) for attribute, _ in COMPLAINT_COLUMNS
            }
            by_category = window.counts(item.sku_normalized, totals)
            facts.append(
                SkuFact(
                    sku=item.sku,
                    sku_normalized=item.sku_normalized,
                    quantity=int(item.total_qty or 0),
                    total_qty=int(item.total_qty or 0),
                    total_orders=int(item.total_orders or 0),
                    total_count=int(item.total_count or 0),
                    complaints=by_category,
                    total_complaints=sum(by_category.values()),
                    complaints_are_dated=window.is_dated(item.sku_normalized),
                    unfilterable_complaints=window.unfilterable(item.sku_normalized, totals),
                    shopify_sales=int(sold or 0),
                    revenue_paise=int(revenue_paise or 0),
                )
            )
        return facts

    def complaint_scope(self, workspace_id: int, *, since: date, until: date) -> ComplaintScope:
        """Whether this workspace's complaint figures follow the range.

        For callers that want the note without building every fact.
        """
        return complaints_repository.resolve(self._db, workspace_id, since=since, until=until).scope

    def window_units(self, workspace_id: int, *, since: date, until: date) -> int:
        """Every unit the store sold in the window, matched to the sheet or not.

        **The denominator for the KPI card's Shopify Sales %** — the question
        there is how much of the store's sales the sheet accounts for, so it has
        to be read separately from the facts. A card whose denominator came from
        the same left join as its numerator would always read 100%.

        The per-SKU column divides by the imported SKUs' own sales instead; see
        ``calc.sales_pct``.
        """
        return int(
            self._db.scalar(
                select(func.coalesce(func.sum(SkuDailyMetric.units_sold), 0)).where(
                    SkuDailyMetric.workspace_id == workspace_id,
                    SkuDailyMetric.metric_date >= since,
                    SkuDailyMetric.metric_date <= until,
                )
            )
            or 0
        )
