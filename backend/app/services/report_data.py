"""What goes in each report — design doc §12.1.

Three report types, one shape: a title, a set of columns, and rows. Everything
here reads through :mod:`app.services.analytics`, so a report and the screen it
was exported from cannot disagree — which is the entire point of an export that
someone will paste into an email.

**Money is formatted here, not in the writers.** A CSV, an XLSX and a PDF of
the same report must show the same number, and the only way to guarantee that
is to decide it once. Rupee values are emitted as plain decimal strings rather
than the lakh/crore abbreviation the UI uses: a spreadsheet has to be able to
sum the column, and "₹8.42 L" is not a number.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.core.calc import format_pct
from app.models import MAX_REPORT_ROWS
from app.repositories.analytics import SkuFactRepository
from app.services import analytics, insights
from app.services.report_tables import Align, Column, ReportTable

#: Stated on every report whose figures include Shopify sales.
#:
#: Fully refunded, voided and cancelled orders are excluded from the rollup.
#: *Partially* refunded ones are not, and cannot be: Shopify reports the refund
#: against the order, and this project stores line items without a refunded
#: quantity, so there is nothing to subtract per SKU. Saying so on the report is
#: the honest option — the alternative is a number that looks exact and is
#: quietly high.
REFUND_NOTE = "Partially refunded orders are counted in full; fully refunded orders are excluded."

#: Re-exported so every existing ``from app.services.report_data import Column,
#: ReportTable`` keeps working. They moved to their own module so this one can
#: call into ``insights`` without a cycle — see ``report_tables``.
__all__ = ["BUILDERS", "Align", "Column", "ReportTable", "build"]


def rupees(paise: int | None) -> str:
    """Paise to a plain decimal a spreadsheet can add up."""
    return f"{(paise or 0) / 100:.2f}"


def _int(value: int | None) -> str:
    return "" if value is None else str(value)


def _scope(shown: int, total: int) -> str:
    """How much of the workspace this report is showing.

    Every per-SKU report is ordered by Shopify sales, so a limited one really is
    the top N — but it has to say so. A report that silently stops at fifty
    reads as a workspace with fifty SKUs, and the totals beside it would then
    look wrong rather than merely partial.
    """
    if shown >= total:
        return f"{total} SKUs"
    return f"Showing top {shown} of {total} SKUs by Shopify sales"


def financial_year_start(today: date | None = None) -> date:
    """1 April of the current Indian financial year.

    The prototype's range control offers "This financial year" and this is an
    Indian retail business, so the year turns on 1 April rather than 1 January.
    """
    today = today or datetime.now(UTC).date()
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def range_days_for(option: str) -> tuple[int, str]:
    """Translate a range option into (days, human label)."""
    if option == "fy":
        start = financial_year_start()
        days = (datetime.now(UTC).date() - start).days + 1
        # Built by hand rather than with strftime("%-d"): that flag is a glibc
        # extension and raises ValueError on Windows, which is what this runs on.
        stamp = f"{start.day} {start.strftime('%b %Y')}"
        return days, f"This financial year (from {stamp})"
    days = int(option)
    return days, f"Last {days} days"


def _sku_rows(
    db: Session, *, workspace_id: int, days: int, limit: int
) -> tuple[list[analytics.SkuRow], int]:
    rows, total, _scope = analytics.sku_table(
        db, workspace_id=workspace_id, days=days, limit=limit, offset=0
    )
    return rows, total


def inventory_report(
    db: Session, *, workspace_id: int, days: int, limit: int = MAX_REPORT_ROWS
) -> ReportTable:
    """The sheet's own figures: stock, orders and the complaint tally."""
    rows, total = _sku_rows(db, workspace_id=workspace_id, days=days, limit=limit)
    body = [
        (
            row.sku,
            str(row.quantity),
            str(row.total_qty),
            str(row.total_count),
            str(row.total_orders),
            str(row.total_complaints),
        )
        for row in rows
    ]
    return ReportTable(
        title="Inventory report",
        subtitle=f"{_scope(len(body), total)} from the imported sheet",
        columns=(
            Column("SKU"),
            Column("Quantity", "right"),
            Column("Total Qty", "right"),
            Column("Total Count", "right"),
            Column("Total Orders", "right"),
            Column("Total Complaints", "right"),
        ),
        rows=body,
        truncated=total > len(body),
    )


def sales_report(
    db: Session, *, workspace_id: int, days: int, limit: int = MAX_REPORT_ROWS
) -> ReportTable:
    """What Shopify sold, against what the sheet holds. Matched by SKU."""
    rows, total = _sku_rows(db, workspace_id=workspace_id, days=days, limit=limit)
    body = [
        (
            row.sku,
            str(row.shopify_sales),
            format_pct(row.shopify_sales_pct),
            str(row.quantity),
            str(row.total_orders),
        )
        for row in rows
    ]
    return ReportTable(
        title="Sales report",
        subtitle=f"{_scope(len(body), total)} over the last {days} days. {REFUND_NOTE}",
        columns=(
            Column("SKU"),
            Column("Shopify Sales", "right"),
            Column("Shopify Sales %", "right"),
            Column("Quantity", "right"),
            Column("Total Orders", "right"),
        ),
        rows=body,
        truncated=total > len(body),
    )


def sku_performance_report(
    db: Session, *, workspace_id: int, days: int, limit: int = MAX_REPORT_ROWS
) -> ReportTable:
    """The SKU Performance page, as a file.

    **Delegates to the page's own code path** rather than restating it. It used
    to build its own table and had drifted from the screen in three ways at
    once: ordered by Shopify sales where the page orders by total complaints,
    carrying both ``Quantity`` and ``Total Qty`` where the page shows one
    quantity, and missing the per-category breakdown entirely. Someone
    reconciling the download against the screen would have found three
    differences and no way to tell which was authoritative.

    ``limit`` is the Top-N cut. The Reports centre passes 50 when "Export Top 50
    only" is ticked and the full cap otherwise, so the same switch that gives
    the page its top fifty gives the file its top fifty — of the same ordering.
    """
    repository = SkuFactRepository(db)
    since, until = analytics.window(days)
    facts = repository.facts(workspace_id, since=since, until=until)

    rows, total = insights.performance(
        facts,
        filters=insights.Filters(),
        sort=insights.DEFAULT_SORT,
        descending=insights.DEFAULT_DESCENDING,
        limit=limit,
        offset=0,
    )
    return insights.as_report_table(rows, days=days, total=total, truncated=total > len(rows))


def dashboard_report(
    db: Session, *, workspace_id: int, days: int, limit: int = MAX_REPORT_ROWS
) -> ReportTable:
    """The dashboard's own figures: the six cards, and the context behind them.

    Metric-and-value rather than a row per SKU, because that is what the
    dashboard *is* — the per-SKU tables are the other three reports, and
    repeating one of them here would make this a duplicate with a different name.

    It reads ``analytics.kpis``, the same call the dashboard renders from, so a
    snapshot and the screen it was taken from cannot disagree. ``limit`` is
    accepted and unused: this report is nine rows whatever the cap, and the
    signature is the one ``build`` calls every builder with.
    """
    k = analytics.kpis(db, workspace_id=workspace_id, days=days)

    body = [
        ("Total SKUs", _int(k.total_skus)),
        ("Total quantity", _int(k.total_quantity)),
        ("Shopify sales (matched)", _int(k.shopify_sales)),
        ("Shopify sales %", format_pct(k.shopify_sales_pct)),
        # The denominator, so the percentage above can be checked rather than
        # taken on trust — the same reason the card states it on screen.
        ("Shopify sales (all SKUs)", _int(k.shopify_sales_all)),
        ("Total orders", _int(k.total_orders)),
        ("Total complaints", _int(k.total_complaints)),
        ("Low stock SKUs", _int(k.low_stock)),
        ("Low stock threshold", _int(k.low_stock_threshold)),
        ("Shopify revenue (matched)", rupees(k.revenue_paise)),
    ]
    return ReportTable(
        title="Dashboard snapshot",
        subtitle=f"Figures over the last {days} days. {REFUND_NOTE}",
        columns=(Column("Metric"), Column("Value", "right")),
        rows=body,
    )


def sku_matching_report(
    db: Session, *, workspace_id: int, days: int, limit: int = MAX_REPORT_ROWS
) -> ReportTable:
    """Which imported SKUs Shopify sold, and which it did not.

    Unmatched first — an unmatched SKU is the finding, and burying it among
    matched ones makes the report something you have to filter before it says
    anything.

    **The statistics come from every imported SKU, never from the rows shown.**
    That distinction is the whole bug this replaced. ``_sku_rows`` applies the
    limit in SQL ordered by sales descending, so unmatched SKUs — which have
    zero sales — sit at the very bottom and a Top 50 page contains none of them.
    Counting the page then produced "0 of 1641 imported SKUs had no Shopify
    sale" while 77 of them had: a limited view was reported as a global fact.

    So the ranking is asked for separately and in full, and the limit is applied
    afterwards to the rows only. A limited report says which rows it is showing;
    it never restates the totals to match them.

    "Unmatched" means no Shopify sale in the window under the normalised SKU. It
    does not mean the SKU is wrong: a product that simply did not sell in the
    last thirty days is unmatched too, which is why the window is stated.

    The reverse gap — SKUs Shopify sold that the sheet has never heard of — is
    not here. This report is per imported SKU, and those have no row to sit on.
    """
    # Every SKU, so the statistics are the workspace's rather than the page's.
    rows, total = _sku_rows(db, workspace_id=workspace_id, days=days, limit=MAX_REPORT_ROWS)
    unmatched = sum(1 for row in rows if row.shopify_sales == 0)

    # Unmatched first, then by complaint weight, then by name: the order someone
    # chasing a mismatch would sort it into by hand.
    ordered = sorted(
        rows,
        key=lambda r: (r.shopify_sales > 0, -r.total_complaints, r.sku_normalized),
    )
    shown = ordered[:limit]

    matched_note = (
        f"{unmatched} of {total} imported SKUs had no Shopify sale in the last {days} days"
    )
    subtitle = (
        f"Showing {len(shown)} of {total} SKUs. {matched_note}"
        if len(shown) < total
        else matched_note
    )

    body = [
        (
            row.sku,
            _int(row.total_qty),
            _int(row.total_complaints),
            _int(row.shopify_sales),
            "Matched" if row.shopify_sales > 0 else "Unmatched",
        )
        for row in shown
    ]
    return ReportTable(
        title="SKU matching report",
        subtitle=subtitle,
        columns=(
            Column("SKU"),
            Column("Imported Quantity", "right"),
            Column("Complaint Quantity", "right"),
            Column("Shopify Sales", "right"),
            Column("Match Status"),
        ),
        rows=body,
        truncated=total > len(body),
    )


BUILDERS: dict[str, Callable[..., ReportTable]] = {
    "inventory": inventory_report,
    "sales": sales_report,
    "sku_performance": sku_performance_report,
    "dashboard": dashboard_report,
    "sku_matching": sku_matching_report,
}


def build(
    db: Session, *, kind: str, workspace_id: int, days: int, limit: int = MAX_REPORT_ROWS
) -> ReportTable:
    """Build a report, reading at most ``limit`` rows.

    The preview passes a small limit so it costs a small query. Building the
    whole report and slicing afterwards meant every change of type or range on
    the Reports page paid for a full export — 4.9 s for Sales on the real store.
    ``truncated`` still reflects the true total, which is counted, not inferred.
    """
    builder = BUILDERS[kind]
    return builder(db, workspace_id=workspace_id, days=days, limit=limit)
