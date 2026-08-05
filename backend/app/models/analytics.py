"""The materialised metrics layer — plan §4.1, Layer 1.

One row per (workspace, SKU, day). Everything the dashboard and the analytics
lenses ask for is a ``GROUP BY`` over this table rather than over
``order_line_items``.

**Why this table exists, measured rather than assumed.** Aggregating the raw
line items for one widget takes ~684 ms at 429,000 rows on this machine, and
the store produces roughly 2.3 million rows in a 90-day window — about 3.7 s
per query, with seven widgets on the dashboard. Rolled up, the same window is
tens of thousands of rows and the queries are single-digit milliseconds. Plan
§4.2 sized the system at 100,000 line items; the real figure is ~23× that, and
this table is what absorbs the difference.

**Not written during ingest.** The rollup is derived, so a bug in it is fixed
by recomputing rather than by a backfill, and the sync path stays as simple and
as fast as it can be (plan §4.1).

**Cancelled and refunded orders are excluded here, once**, so no downstream
query has to remember to — the omission that makes a reconciliation tool
quietly wrong.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import UtcDateTime, utcnow


class SkuDailyMetric(Base):
    """Units, revenue and order count for one SKU on one day."""

    __tablename__ = "sku_daily_metrics"
    __table_args__ = (
        # Composite primary key rather than a surrogate id: the grain *is* the
        # identity, and it gives per-SKU lookups the reconciliation table needs.
        Index("ix_sku_daily_metrics_date", "workspace_id", "metric_date"),
        # Covering index. Every measure the dashboard sums is included, so a
        # date-range aggregate is answered from the index without touching the
        # table at all. Measured on 91,190 rows: the KPI sums went from 278 ms
        # to 26 ms, the trend from 299 ms to 26 ms, and the top-sellers ranking
        # from 551 ms to 78 ms. The cost is one more index on a derived table
        # that is rebuilt rather than migrated.
        Index(
            "ix_sku_daily_metrics_cover",
            "workspace_id",
            "metric_date",
            "sku_normalized",
            "units_sold",
            "revenue_paise",
            "order_count",
        ),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    sku_normalized: Mapped[str] = mapped_column(String(120), primary_key=True)
    metric_date: Mapped[date] = mapped_column(Date, primary_key=True)

    units_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Paise, exact (plan §4.5). Line revenue is quantity × unit price less the
    # line's discount; shipping and tax are order-level and excluded — see Q2.
    revenue_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    computed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SkuDailyMetric {self.sku_normalized!r} {self.metric_date} u={self.units_sold}>"


class SkuDailyComplaint(Base):
    """Complaints for one SKU on one day, broken down by category.

    **The table that makes Complaint Rate % a rate.** Until it existed the
    complaint counts lived only as running totals on ``inventory_items``, with
    no date on them at all — so a "rate over the last 30 days" divided an
    all-time numerator by a 30-day denominator and produced figures like 866%.

    Written at import from the raw complaint export, which carries one row per
    complaint with the date on it. The aggregated sheet format has no dates and
    writes nothing here.

    **Nothing reads this for analytics.** Complaint figures come from the
    running totals on ``inventory_items``, which is the whole tally regardless
    of range. The dates are captured because the export carries them and
    discarding them would mean a re-import to get them back; they are here for
    whenever a per-period complaint figure is wanted.

    Same grain and same shape as ``SkuDailyMetric`` on purpose: the two are
    joined over the same window on every read, and matching their keys keeps
    that a pair of indexed range scans.
    """

    __tablename__ = "sku_daily_complaints"
    __table_args__ = (
        Index("ix_sku_daily_complaints_date", "workspace_id", "complaint_date"),
        # Covering, for the same reason as the metrics table: the windowed sum
        # per SKU is answered from the index without touching the rows.
        Index(
            "ix_sku_daily_complaints_cover",
            "workspace_id",
            "complaint_date",
            "sku_normalized",
            "total_complaints",
        ),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    sku_normalized: Mapped[str] = mapped_column(String(120), primary_key=True)
    complaint_date: Mapped[date] = mapped_column(Date, primary_key=True)

    #: The ten categories, one column each — the same list and the same names as
    #: ``inventory_items`` carries, so a breakdown reads identically from either.
    item_defect_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_defect_complete: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_damage_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_damage_complete: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_wrong_parcel: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    electronics_nonworking_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    electronics_nonworking_complete: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_part: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_mismatch_wrong_item: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Stored rather than summed on read. It is the figure every window query
    #: wants, and keeping it in the covering index is what makes the range scan
    #: index-only.
    total_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    imported_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<SkuDailyComplaint {self.sku_normalized!r} "
            f"{self.complaint_date} n={self.total_complaints}>"
        )
