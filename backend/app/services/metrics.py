"""Building the rollup — plan §4.1, Layer 1.

Recomputed from ``order_line_items`` rather than incremented during ingest, so
a bug here is fixed by re-running rather than by a backfill.

**Incremental by date.** A refresh deletes and recomputes only the days the
sync touched. A full rebuild is available and, at the measured rate, takes a
couple of seconds — but doing it after every sync would be work the user waits
through for no gain.

**Set-based, not row-by-row.** The whole rollup is one INSERT…SELECT. Pulling
2.3 million line items into Python to add them up would be both slower and
pointless: this is exactly what a database is for, and the SQL is plain enough
to behave identically on PostgreSQL (plan §4.4).

**Staged, then swapped.** The expensive part of a rebuild is reading and
grouping the line items, and it used to happen inside the write transaction —
so SQLite's single write lock was held for the whole 69 seconds and every other
writer in the application, `/auth/refresh` included, was locked out. The
aggregation now runs into a temporary table, which lives in the connection's own
temporary database and takes no lock on the main one; only the swap does, and
the swap is a bulk copy of rows already computed. Measured on the live database:
69s of lock became ~11s. It stays one transaction rather than several so that a
reader sees the old rollup or the new one and never a half-replaced one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Column,
    Connection,
    Date,
    Integer,
    MetaData,
    Select,
    String,
    Table,
    delete,
    func,
    insert,
    literal,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Order, OrderLineItem, SkuDailyMetric, SyncRun, sale_filters, utcnow
from app.models.localtime import local_day
from app.services.workspace_time import workspace_offset_minutes

log = logging.getLogger(__name__)


#: The shape the aggregation produces, as a table this connection can write to
#: without touching the main database's write lock.
#:
#: Its own MetaData, deliberately: it must never appear in a migration or in
#: `Base.metadata.create_all`. It exists for the length of one rebuild and is
#: created and dropped by `_stage` and `_drop_staging`.
_STAGING = Table(
    "metrics_staging",
    MetaData(),
    Column("sku_normalized", String(120)),
    Column("metric_date", Date),
    Column("units_sold", Integer),
    Column("revenue_paise", Integer),
    Column("order_count", Integer),
    schema="temp",
)


def _drop_staging(conn: Connection) -> None:
    conn.execute(text("DROP TABLE IF EXISTS temp.metrics_staging"))


def _stage(conn: Connection, aggregation: Select[Any]) -> None:
    """Compute the rollup into a TEMP table, holding no lock on the main file.

    The aggregation expression is the caller's, unchanged — the figures a
    rebuild produces have to be identical to the ones it produced before, and
    the surest way to guarantee that is to run the same query.
    """
    _drop_staging(conn)
    conn.execute(
        text(
            "CREATE TEMP TABLE metrics_staging ("
            "  sku_normalized TEXT,"
            "  metric_date TEXT,"
            "  units_sold INTEGER,"
            "  revenue_paise INTEGER,"
            "  order_count INTEGER"
            ")"
        )
    )
    conn.execute(
        insert(_STAGING).from_select(
            ["sku_normalized", "metric_date", "units_sold", "revenue_paise", "order_count"],
            aggregation,
        )
    )


@dataclass(frozen=True)
class RebuildResult:
    rows_written: int
    days_covered: int
    duration_ms: int
    since: date | None
    until: date | None


def refresh(
    db: Session,
    *,
    workspace_id: int,
    since: date | None = None,
    until: date | None = None,
) -> RebuildResult:
    """Recompute the rollup for a date range, or for everything.

    Delete-then-insert over the affected range rather than an upsert: a SKU
    that stopped selling on a day must lose its row, and an upsert would leave
    the old number behind.
    """
    started = datetime.now(UTC)

    # Bucketed on the workspace's own calendar day, not UTC's. 17.7% of this
    # store's orders land between 00:00 and 05:30 UTC, which is the previous day
    # in India — so a UTC bucket files a sixth of every day's sales one day late.
    day = local_day(Order.processed_at, workspace_offset_minutes(db, workspace_id))
    conditions = sale_filters()
    if since is not None:
        conditions.append(day >= since.isoformat())
    if until is not None:
        conditions.append(day <= until.isoformat())

    aggregation = (
        select(
            OrderLineItem.sku_normalized.label("sku_normalized"),
            day.label("metric_date"),
            func.sum(OrderLineItem.quantity).label("units_sold"),
            # Line revenue = units × unit price − the line's discount. Shipping
            # and tax sit on the order, not the line, and are excluded; Q2 asks
            # whether that is the intended definition.
            func.sum(
                OrderLineItem.quantity * func.coalesce(OrderLineItem.price_paise, 0)
                - func.coalesce(OrderLineItem.total_discount_paise, 0)
            ).label("revenue_paise"),
            func.count(func.distinct(OrderLineItem.order_id)).label("order_count"),
        )
        .join(Order, OrderLineItem.order_id == Order.id)
        .where(OrderLineItem.workspace_id == workspace_id, *conditions)
        .group_by(OrderLineItem.sku_normalized, day)
    )

    # ---------------------------------------------------------------- stage
    #
    # The aggregation runs into a TEMP table first, and this is the whole point
    # of the rewrite. It used to run *after* the DELETE below, which had already
    # taken SQLite's single write lock — so the 55 seconds of reading and
    # grouping 2.3 million line items happened with every other writer in the
    # application locked out. On the live database that was 69 seconds during
    # which `/auth/refresh` could not write, and a renewal that cannot write is a
    # 500 that used to read as "signed out".
    #
    # A TEMP table lives in the connection's own temporary database, so writing
    # to it takes no lock on the main one. Verified rather than assumed: with a
    # write lock held from another connection, `CREATE TEMP TABLE … AS SELECT`
    # completes in a millisecond while an insert into a main table waits out the
    # busy timeout and fails.
    #
    # **One pinned connection, start to finish.** A TEMP table belongs to the
    # connection that made it, and a Session hands its connection back to the
    # pool whenever it commits — so staging on the Session and swapping after the
    # commit below went looking for a table that had left with the connection.
    # `engine.connect()` holds one for the whole block, across both transactions.
    #
    # The caller's Session is untouched: the swap commits on this connection, and
    # the Session sees the new rows on its next read.
    scope = delete(SkuDailyMetric).where(SkuDailyMetric.workspace_id == workspace_id)
    if since is not None:
        scope = scope.where(SkuDailyMetric.metric_date >= since)
    if until is not None:
        scope = scope.where(SkuDailyMetric.metric_date <= until)

    with db.get_bind().engine.connect() as conn:
        try:
            _stage(conn, aggregation)
            staged, days_covered = conn.execute(
                select(func.count(), func.count(func.distinct(_STAGING.c.metric_date))).select_from(
                    _STAGING
                )
            ).one()

            # **End the read snapshot before writing anything.**
            #
            # Staging leaves this connection holding a read transaction on the
            # main database. A DELETE issued inside it is an *upgrade*, and
            # SQLite fails an upgrade with SQLITE_BUSY the instant another
            # connection has written since the snapshot was taken —
            # `busy_timeout` does not apply to that case, so there is no waiting
            # and no retry. It cost a whole rebuild in testing: "database is
            # locked" on the DELETE, immediately, against a writer committing
            # every two seconds.
            #
            # Committing here writes nothing to the main database — the staged
            # rows went to this connection's temporary one — but it releases the
            # snapshot, so the swap below opens a fresh transaction whose first
            # statement is a write and therefore waits properly. A commit rather
            # than a rollback, because a TEMP table created in a rolled-back
            # transaction goes with it.
            conn.commit()

            # --------------------------------------------------------- swap
            #
            # One transaction, so a reader sees the old rollup or the new one and
            # never a half-deleted one. That was already true and is the property
            # this rewrite had to preserve: what changed is the transaction's
            # *contents*, now a bulk copy of pre-computed rows rather than the
            # aggregation itself.
            conn.execute(scope)
            conn.execute(
                insert(SkuDailyMetric).from_select(
                    [
                        "workspace_id",
                        "sku_normalized",
                        "metric_date",
                        "units_sold",
                        "revenue_paise",
                        "order_count",
                        "computed_at",
                    ],
                    select(
                        literal(workspace_id),
                        _STAGING.c.sku_normalized,
                        _STAGING.c.metric_date,
                        _STAGING.c.units_sold,
                        _STAGING.c.revenue_paise,
                        _STAGING.c.order_count,
                        literal(utcnow()),
                    ),
                )
            )
            conn.commit()
        finally:
            # The connection is about to go back to the pool carrying its
            # temporary database with it, so a staging table left behind is one
            # the next rebuild to draw this connection would find.
            #
            # The rollback comes first and matters on the failing path: after a
            # statement raises, the connection refuses further work until its
            # transaction is unwound, so a DROP issued straight away would raise
            # `PendingRollbackError` over the top of the real error. On the
            # succeeding path there is no transaction open and it does nothing.
            # And the cleanup cannot be allowed to become the failure either —
            # it is tidying, and the caller needs the exception that got here.
            try:
                conn.rollback()
                _drop_staging(conn)
                conn.commit()
            except SQLAlchemyError:  # pragma: no cover - tidying, never fatal
                log.warning("could not drop the staging table", exc_info=True)

    written = int(staged or 0)
    covered = int(days_covered or 0)

    # Refresh the planner's statistics. This is not housekeeping — without it
    # the covering index on this table actively misleads SQLite: it picks the
    # date-ordered index for the per-SKU joins in the reconciliation table and
    # the vendor breakdown, turning indexed seeks into scans. Measured on
    # 91,190 rows, the whole dashboard went from 15.3 s to 0.38 s once these
    # statistics existed. ANALYZE is understood by SQLite and PostgreSQL alike,
    # so this does not breach the dialect-agnostic rule (plan §4.4).
    #
    # It writes, so it takes the lock again — a second window of about two
    # seconds, after the swap rather than inside it. Deliberately outside: the
    # swap is what has to be atomic, and folding an advisory rewrite of the
    # statistics tables into it would lengthen the transaction that readers and
    # writers actually wait on.
    try:
        db.execute(text("ANALYZE"))
        db.commit()
    except SQLAlchemyError:  # pragma: no cover - advisory, never fatal
        log.warning("could not refresh planner statistics", exc_info=True)

    duration = int((datetime.now(UTC) - started).total_seconds() * 1000)
    log.info(
        "metrics refreshed workspace=%s rows=%s days=%s in %sms",
        workspace_id,
        written,
        covered,
        duration,
    )
    return RebuildResult(
        rows_written=written,
        days_covered=covered,
        duration_ms=duration,
        since=since,
        until=until,
    )


def refresh_recent(db: Session, *, workspace_id: int, days: int = 120) -> RebuildResult:
    """Refresh the window a sync could plausibly have touched.

    Called after a sync. Bounded rather than full so the cost stays flat as
    order history accumulates.
    """
    since = (datetime.now(UTC) - timedelta(days=days)).date()
    return refresh(db, workspace_id=workspace_id, since=since)


def is_syncing(db: Session, *, workspace_id: int) -> bool:
    """True while a sync run is queued or in flight.

    Read beside :func:`is_stale`, which it overrides: figures being rebuilt are
    not figures that need attention.
    """
    return (
        db.scalar(
            select(func.count(SyncRun.id)).where(
                SyncRun.workspace_id == workspace_id,
                SyncRun.status.in_(("queued", "running")),
            )
        )
        or 0
    ) > 0


def is_stale(db: Session, *, workspace_id: int) -> bool:
    """True when orders exist that the rollup has never seen **and nothing is
    working on it**.

    The second half is not a nicety. A sync commits its orders page by page and
    recomputes at the end, so from the first page landing until the rollup runs
    there are always orders the rollup has not seen — every sync passes through
    this state on its way to succeeding. Without the guard the Dashboard put up
    "Sales figures are behind the last sync", with a Retry button beside it,
    during a sync that was working perfectly, and the retry it offered would
    have been refused as already-running.

    So a run in flight answers False: the figures are not behind, they are being
    rebuilt, and the page says *that* instead. What is left is the honest case —
    orders in the database, no run working on them, and a rollup that never
    caught up, which now only happens when the automatic recompute failed.
    """
    if is_syncing(db, workspace_id=workspace_id):
        return False
    latest_order = db.scalar(
        select(func.max(Order.synced_at)).where(Order.workspace_id == workspace_id)
    )
    if latest_order is None:
        return False
    computed = db.scalar(
        select(func.max(SkuDailyMetric.computed_at)).where(
            SkuDailyMetric.workspace_id == workspace_id
        )
    )
    if computed is None:
        return True
    return bool(computed < latest_order)
