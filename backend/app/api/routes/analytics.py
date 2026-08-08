"""Analytics endpoints, serving two pages that must not become one.

* ``/overview``, ``/kpis``, ``/trend``, ``/skus`` — the **dashboard**: six cards,
  a trend and the flat SKU table. A quick look at what was imported.
* ``/insights``, ``/performance`` — the **Analytics** page: eight KPIs, rankings,
  distributions, generated findings and a filterable performance table. The
  questions you ask after the quick look.

They share the trend and the complaint column set and nothing else. The dashboard
reads through ``services.analytics``, which queries per widget; Analytics reads
through ``repositories.analytics`` once and derives in ``services.insights``.
Keeping the two apart is deliberate — the dashboard is not allowed to get slower
because Analytics grew a panel, and Analytics is not allowed to be constrained by
what the dashboard already returns.

Everything either page shows comes from the imported sheet or from Shopify units
sold. There is no product catalogue, no variant and no vendor left to describe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, SettingsDep, enforce_rate_limit
from app.core.errors import AppError
from app.models import COMPLAINT_COLUMNS, InventoryItem, SkuDailyMetric
from app.repositories.analytics import SkuFact, SkuFactRepository
from app.repositories.complaints import ComplaintScope
from app.schemas.analytics import (
    SORT_PATTERN,
    STATUS_PATTERN,
    AnalyticsInsights,
    AnalyticsOverview,
    ComplaintAnalyticsPayload,
    ComplaintColumn,
    ComplaintScopePayload,
    InsightKpiPayload,
    InventoryInsightsPayload,
    KpiPayload,
    NamedCountPayload,
    PerformancePage,
    PerformanceRowPayload,
    QuickInsightPayload,
    RankedSkuPayload,
    RankingsPayload,
    RebuildResultPayload,
    SalesAnalyticsPayload,
    SkuRowPayload,
    SkuTablePage,
    TrendPayload,
    TrendPointPayload,
)
from app.services import analytics as analytics_service
from app.services import insights as insights_service
from app.services import metrics as metrics_service
from app.services import report_files

log = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"], prefix="/analytics")

#: How many rows an export carries. The same order of magnitude as the sheet
#: itself, so a whole workspace fits; a file that quietly ended at 50 rows would
#: be worse than no export at all.
EXPORT_ROW_CAP = 20_000

#: The complaint attributes a filter may name, from the model.
_COMPLAINT_FIELDS = {attribute for attribute, _ in COMPLAINT_COLUMNS}


class UnknownComplaintCategoryError(AppError):
    code = "unknown_complaint_category"
    status_code = 422
    message = "That isn't one of the complaint categories."
    next_step = "Pick a category from the filter list, or clear the filter."


#: Ranges the range control offers. Validated rather than free-form so a caller
#: cannot ask for a window the rollup was never built over.
RangeQuery = Query(default=analytics_service.DEFAULT_RANGE, ge=1, le=365)

#: The longest custom window, matching the cap on ``days`` above. The rollup is
#: built over a bounded history; asking for more would return a window the data
#: cannot fill and read as a collapse in sales.
MAX_RANGE_DAYS = 365

#: The complaint set, as the client should render it. Derived from the model, so
#: adding a category is one edit in one place.
COMPLAINT_PAYLOAD = [
    ComplaintColumn(field=attribute, header=header) for attribute, header in COMPLAINT_COLUMNS
]


def _kpis(db: DbDep, workspace_id: int, days: int) -> KpiPayload:
    computed = db.scalar(
        select(func.max(SkuDailyMetric.computed_at)).where(
            SkuDailyMetric.workspace_id == workspace_id
        )
    )
    figures = analytics_service.kpis(db, workspace_id=workspace_id, days=days)
    return KpiPayload(
        **{k: v for k, v in figures.__dict__.items() if k != "complaint_scope"},
        complaint_scope=_scope_payload(figures.complaint_scope),
        days=days,
        stale=metrics_service.is_stale(db, workspace_id=workspace_id),
        syncing=metrics_service.is_syncing(db, workspace_id=workspace_id),
        last_computed_at=computed,
    )


def _scope_payload(scope: ComplaintScope) -> ComplaintScopePayload:
    """The scope, in one shape for every page that shows a complaint figure."""
    return ComplaintScopePayload(
        filtered_by_date=scope.filtered_by_date,
        dated_skus=scope.dated_skus,
        undated_skus=scope.undated_skus,
        undated_complaints=scope.undated_complaints,
    )


def _trend(db: DbDep, workspace_id: int, days: int) -> TrendPayload:
    computed = analytics_service.trend(db, workspace_id=workspace_id, days=days)
    return TrendPayload(
        points=[
            TrendPointPayload(day=p.day, units=p.units, revenue_paise=p.revenue_paise)
            for p in computed.points
        ],
        previous=[
            TrendPointPayload(day=p.day, units=p.units, revenue_paise=p.revenue_paise)
            for p in computed.previous
        ],
        days=days,
    )


@router.get("/overview", response_model=AnalyticsOverview, summary="The dashboard, in one call")
def overview(user: CurrentUser, db: DbDep, days: int = RangeQuery) -> AnalyticsOverview:
    workspace_id = user.workspace_id
    has_data = bool(
        db.scalar(
            select(func.count(InventoryItem.id)).where(InventoryItem.workspace_id == workspace_id)
        )
    )
    return AnalyticsOverview(
        kpis=_kpis(db, workspace_id, days),
        trend=_trend(db, workspace_id, days),
        has_data=has_data,
    )


@router.get("/kpis", response_model=KpiPayload, summary="The six summary cards")
def kpis(user: CurrentUser, db: DbDep, days: int = RangeQuery) -> KpiPayload:
    return _kpis(db, user.workspace_id, days)


@router.get("/trend", response_model=TrendPayload, summary="Daily units and revenue")
def trend(user: CurrentUser, db: DbDep, days: int = RangeQuery) -> TrendPayload:
    return _trend(db, user.workspace_id, days)


@router.get("/skus", response_model=SkuTablePage, summary="The main SKU table")
def skus(
    user: CurrentUser,
    db: DbDep,
    days: int = RangeQuery,
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SkuTablePage:
    """Every sheet SKU with its Shopify sales beside it, matched by SKU alone."""
    rows, total, scope = analytics_service.sku_table(
        db,
        workspace_id=user.workspace_id,
        days=days,
        search=search,
        limit=limit,
        offset=offset,
    )
    return SkuTablePage(
        rows=[SkuRowPayload(**row.__dict__) for row in rows],
        complaint_columns=COMPLAINT_PAYLOAD,
        complaint_scope=_scope_payload(scope),
        total=total,
        limit=limit,
        offset=offset,
        days=days,
    )


def _facts_between(
    db: DbDep, workspace_id: int, since: date, until: date
) -> tuple[list[SkuFact], int]:
    """Facts over an explicit window, and the store's total units in it.

    Every figure in a response shares one window. The total is what the KPI
    cards divide by; the SKU table divides by the imported SKUs' own sales and
    derives that itself, so it is not returned for the table's benefit.
    """
    repository = SkuFactRepository(db)
    return (
        repository.facts(workspace_id, since=since, until=until),
        repository.window_units(workspace_id, since=since, until=until),
    )


def _facts(db: DbDep, workspace_id: int, days: int) -> tuple[list[SkuFact], int]:
    """The one read both Analytics endpoints derive from."""
    since, until = analytics_service.window(days)
    repository = SkuFactRepository(db)
    return (
        repository.facts(workspace_id, since=since, until=until),
        repository.window_units(workspace_id, since=since, until=until),
    )


@router.get(
    "/insights",
    response_model=AnalyticsInsights,
    summary="The Analytics page, above the table",
)
def insights(user: CurrentUser, db: DbDep, days: int = RangeQuery) -> AnalyticsInsights:
    """KPIs, sales, complaints, rankings, inventory insights and quick cards.

    All of it from one read of the SKU facts, so no two panels can describe
    different moments — and the eight cards provably agree with the table below,
    because they fold the same list.
    """
    workspace_id = user.workspace_id
    facts, all_units = _facts(db, workspace_id, days)
    computed = db.scalar(
        select(func.max(SkuDailyMetric.computed_at)).where(
            SkuDailyMetric.workspace_id == workspace_id
        )
    )

    return AnalyticsInsights(
        kpis=InsightKpiPayload(**insights_service.kpis(facts, all_units=all_units).__dict__),
        sales=_sales_payload(insights_service.sales(facts, all_units=all_units)),
        complaints=_complaints_payload(insights_service.complaints(facts, all_units=all_units)),
        rankings=RankingsPayload(
            **{
                name: _ranked_payload(rows)
                for name, rows in insights_service.rankings(
                    facts, all_units=all_units
                ).__dict__.items()
            }
        ),
        inventory=_inventory_payload(
            insights_service.inventory_insights(facts, all_units=all_units)
        ),
        quick=[
            QuickInsightPayload(**insight.__dict__)
            for insight in insights_service.quick_insights(facts, all_units=all_units)
        ],
        trend=_trend(db, workspace_id, days),
        complaint_columns=COMPLAINT_PAYLOAD,
        complaint_scope=_scope_payload(insights_service.complaint_scope(facts)),
        days=days,
        has_data=bool(facts),
        stale=metrics_service.is_stale(db, workspace_id=workspace_id),
        syncing=metrics_service.is_syncing(db, workspace_id=workspace_id),
        last_computed_at=computed,
    )


class InvalidDateRangeError(AppError):
    code = "invalid_date_range"
    status_code = 422
    message = "That date range can't be used."
    next_step = "Pick a start date on or before the end date, within the last year."


@dataclass
class PerformanceQuery:
    """The performance table's query string, declared once.

    A dependency rather than a repeated parameter list: the table and its export
    must filter and sort identically, and two copies of ten ``Query(...)``
    declarations would eventually disagree about a default.
    """

    days: int = RangeQuery
    #: An explicit window, for the custom range control. When both are given
    #: they win over ``days``; the presets keep using ``days`` unchanged, so
    #: nothing that already worked has to learn a new parameter.
    # Annotated rather than a Query() default: ruff treats a call in a
    # dataclass default as suspect for any type it cannot prove immutable, and
    # `date` is not on its list. Same behaviour, declared the other way round.
    since: Annotated[date | None, Query()] = None
    until: Annotated[date | None, Query()] = None
    search: str | None = Query(default=None, max_length=120)
    min_sales: int | None = Query(default=None, ge=0)
    max_sales: int | None = Query(default=None, ge=0)
    complaint_category: str | None = Query(default=None, max_length=64)
    min_sales_pct: float | None = Query(default=None, ge=0, le=100)
    min_qty: int | None = Query(default=None, ge=0)
    max_qty: int | None = Query(default=None, ge=0)
    status: str | None = Query(default=None, pattern=STATUS_PATTERN)
    # The shared ordering, so a client that omits `sort` gets the same table the
    # UI shows rather than a different one.
    sort: str = Query(default=insights_service.DEFAULT_SORT, pattern=SORT_PATTERN)
    descending: bool = Query(default=insights_service.DEFAULT_DESCENDING)

    def window(self) -> tuple[date, date]:
        """The period every figure in this response is computed over.

        An explicit pair wins over ``days``. Both bounds are required together:
        a half-given range is a mistake, and guessing the missing end would
        silently answer a different question from the one asked.
        """
        if self.since is not None and self.until is not None:
            if self.until < self.since:
                raise InvalidDateRangeError(
                    detail={"since": self.since.isoformat(), "until": self.until.isoformat()}
                )
            if (self.until - self.since).days + 1 > MAX_RANGE_DAYS:
                raise InvalidDateRangeError(
                    message=f"That range is longer than {MAX_RANGE_DAYS} days.",
                    detail={"days": (self.until - self.since).days + 1},
                )
            return self.since, self.until
        if self.since is not None or self.until is not None:
            raise InvalidDateRangeError(
                message="A custom range needs both a start and an end date."
            )
        return analytics_service.window(self.days)

    def range_days(self) -> int:
        """How many days the resolved window spans — reported back to the UI."""
        since, until = self.window()
        return (until - since).days + 1

    def filters(self) -> insights_service.Filters:
        """Validated and converted. Raises if a complaint category is unknown.

        Checked against the model's own set rather than trusted: an unknown name
        would silently match nothing, which reads as "no results" instead of "you
        asked for a column that doesn't exist".
        """
        if self.complaint_category and self.complaint_category not in _COMPLAINT_FIELDS:
            raise UnknownComplaintCategoryError(
                detail={
                    "received": self.complaint_category,
                    "known": sorted(_COMPLAINT_FIELDS),
                }
            )
        return insights_service.Filters(
            search=self.search,
            min_sales=self.min_sales,
            max_sales=self.max_sales,
            complaint_category=self.complaint_category,
            min_sales_pct=self.min_sales_pct,
            min_qty=self.min_qty,
            max_qty=self.max_qty,
            status=cast("insights_service.Status | None", self.status),
        )


PerformanceQueryDep = Annotated[PerformanceQuery, Depends()]


def _performance_rows(
    db: DbDep, workspace_id: int, query: PerformanceQuery, *, limit: int, offset: int
) -> tuple[list[insights_service.PerformanceRow], int, ComplaintScope]:
    """The page, the filtered total, and whether complaints followed the range.

    The window bounds the Shopify figures always, and the complaint figures for
    the SKUs imported with dates on them. The scope says which those were.
    """
    since, until = query.window()
    facts, _store_units = _facts_between(db, workspace_id, since, until)
    rows, total = insights_service.performance(
        facts,
        filters=query.filters(),
        sort=query.sort,
        descending=query.descending,
        limit=limit,
        offset=offset,
    )
    return rows, total, insights_service.complaint_scope(facts)


@router.get(
    "/performance",
    response_model=PerformancePage,
    summary="The SKU performance table",
)
def performance(
    user: CurrentUser,
    db: DbDep,
    query: PerformanceQueryDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PerformancePage:
    """Every SKU, filtered and sorted, with its status computed."""
    rows, total, scope = _performance_rows(db, user.workspace_id, query, limit=limit, offset=offset)
    return PerformancePage(
        rows=[PerformanceRowPayload(**row.__dict__) for row in rows],
        complaint_columns=COMPLAINT_PAYLOAD,
        complaint_scope=_scope_payload(scope),
        total=total,
        limit=limit,
        offset=offset,
        days=query.range_days(),
        sort=query.sort,
        descending=query.descending,
    )


@router.get(
    "/performance/export",
    summary="The SKU performance table as a file",
    response_class=Response,
)
def performance_export(
    user: CurrentUser,
    db: DbDep,
    query: PerformanceQueryDep,
    fmt: str = Query(default="csv", pattern="^(csv|xlsx)$", alias="format"),
) -> Response:
    """The same rows, filtered the same way, as a download.

    Rendered by ``report_files`` — the writers the Reports module uses. Reusing
    them is not only about duplication: they neutralise cells that a spreadsheet
    would otherwise execute as a formula, and a SKU is user-supplied text.

    Unpaginated on purpose. An export of "the first 50 rows" is a trap, so it
    carries the whole filtered set up to the cap, and says in the file when the
    cap was reached.
    """
    rows, total, _scope = _performance_rows(
        db, user.workspace_id, query, limit=EXPORT_ROW_CAP, offset=0
    )
    table = insights_service.as_report_table(
        rows, days=query.range_days(), total=total, truncated=total > len(rows)
    )
    body = report_files.render(table, fmt)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    log.info("performance export: %s rows as %s", len(rows), fmt)

    return Response(
        content=body,
        media_type=report_files.CONTENT_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="sku-performance-{stamp}.{fmt}"',
            # A filtered export is a snapshot of a moment; caching it would serve
            # yesterday's figures under today's filters.
            "Cache-Control": "no-store",
        },
    )


def _ranked_payload(rows: list[insights_service.RankedSku]) -> list[RankedSkuPayload]:
    return [RankedSkuPayload(**row.__dict__) for row in rows]


def _counts_payload(rows: list[insights_service.NamedCount]) -> list[NamedCountPayload]:
    return [NamedCountPayload(**row.__dict__) for row in rows]


def _one_ranked(row: insights_service.RankedSku | None) -> RankedSkuPayload | None:
    return RankedSkuPayload(**row.__dict__) if row is not None else None


def _sales_payload(computed: insights_service.SalesAnalytics) -> SalesAnalyticsPayload:
    return SalesAnalyticsPayload(
        shopify_sales=computed.shopify_sales,
        shopify_sales_pct=computed.shopify_sales_pct,
        highest=_one_ranked(computed.highest),
        lowest=_one_ranked(computed.lowest),
        top=_ranked_payload(computed.top),
        distribution=_counts_payload(computed.distribution),
    )


def _complaints_payload(
    computed: insights_service.ComplaintAnalytics,
) -> ComplaintAnalyticsPayload:
    return ComplaintAnalyticsPayload(
        total_complaints=computed.total_complaints,
        most_complained=_one_ranked(computed.most_complained),
        categories=_counts_payload(computed.categories),
        top_skus=_ranked_payload(computed.top_skus),
        skus_with_complaints=computed.skus_with_complaints,
    )


def _inventory_payload(
    computed: insights_service.InventoryInsights,
) -> InventoryInsightsPayload:
    return InventoryInsightsPayload(
        high_stock_low_sales=_ranked_payload(computed.high_stock_low_sales),
        low_stock_high_sales=_ranked_payload(computed.low_stock_high_sales),
        zero_sales=_ranked_payload(computed.zero_sales),
        most_complaints=_ranked_payload(computed.most_complaints),
        median_qty=computed.median_qty,
        median_sales=computed.median_sales,
        zero_sales_total=computed.zero_sales_total,
    )


@router.post("/rebuild", response_model=RebuildResultPayload, summary="Recompute the rollup")
def rebuild(
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    days: int | None = Query(default=None, ge=1, le=3650),
) -> RebuildResultPayload:
    """Recompute ``sku_daily_metrics`` from the order lines.

    Runs inline rather than as a job: it is a few seconds at the measured rate,
    and a user who asks for a rebuild is waiting for the answer.
    """
    # Inline means this one holds a request open *and* takes SQLite's write
    # lock to swap the rebuilt rollup in — measured at ~11s on half a million
    # rows. Several at once is the shape that makes the whole app feel stalled.
    enforce_rate_limit(settings, user, operation="rebuild", what="recompute")
    result = (
        metrics_service.refresh_recent(db, workspace_id=user.workspace_id, days=days)
        if days
        else metrics_service.refresh(db, workspace_id=user.workspace_id)
    )
    return RebuildResultPayload(
        rows_written=result.rows_written,
        days_covered=result.days_covered,
        duration_ms=result.duration_ms,
    )
