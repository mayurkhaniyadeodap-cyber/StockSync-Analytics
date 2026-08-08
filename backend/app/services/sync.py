"""Pulling orders and their line items from Shopify.

The shape of a sync, and why:

**It owns its own session.** A sync runs on a worker thread long after the
request that started it returned, so it cannot borrow the request's session.
It opens one, and commits in batches.

**It commits per page, not per run.** SQLite serialises writes (plan §4.5), so
one transaction spanning a 100k-line order pull would block every other write
for its whole duration. Committing each page keeps the lock held for
milliseconds at a time, and has the side effect that a crash mid-sync leaves
the pages that already landed rather than rolling back an hour of work.

**Progress is a row, not memory.** Each page updates ``sync_runs``. The UI
polls that row, so progress survives a reload, and a process restart leaves a
run visibly stuck rather than vanished.

**Orders only.** Order line items carry ``sku_at_sale``, so sales attribute to
an uploaded SKU without the product catalogue ever being fetched. That is the
whole reason the catalogue was dropped: it made ``read_products`` a hard
requirement for a figure that never depended on it.

**Partial is a real outcome.** If the order stage stops part-way — a revoked
scope, a rate limit that outlasts its retries — the run is ``partial``, the
pages that landed are kept, and the cursor is stored so the next run resumes
instead of restarting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import crypto
from app.core.errors import AppError
from app.db.session import get_session_factory
from app.models import (
    Order,
    OrderLineItem,
    ShopifyConnection,
    SyncRun,
    normalize_sku,
    utcnow,
)
from app.repositories import (
    OrderRepository,
    ShopifyConnectionRepository,
    SyncRunRepository,
)
from app.services import activity, metrics
from app.services import shopify as shopify_service
from app.services.shopify_client import (
    Page,
    ShopifyAuthError,
    ShopifyClient,
    ShopifyError,
    ShopifyScopeError,
)
from app.workers import runner

log = logging.getLogger(__name__)


class SyncAlreadyRunningError(AppError):
    code = "sync_already_running"
    status_code = 409
    message = "A sync is already running."
    next_step = "Wait for it to finish, then start another."


class NothingToRetryError(AppError):
    """Retry was asked for when the last run succeeded.

    Unreachable from the UI, which only offers the control on a failure — but an
    endpoint that starts a full sync because someone posted to it twice is a
    sharp edge whether or not a screen exposes it.
    """

    code = "nothing_to_retry"
    status_code = 409
    message = "The last sync finished successfully."
    next_step = "There is nothing to retry. Import a sheet to start a new sync."


class NotConnectedError(AppError):
    code = "shopify_not_connected"
    status_code = 404
    message = "No Shopify store is connected."
    next_step = "Connect a store first."


@dataclass
class StageOutcome:
    """What one stage achieved, and where it stopped if it failed.

    ``error`` is an ``AppError`` rather than a ``ShopifyError`` because not
    every stage talks to Shopify: recomputing the rollup is a stage of the run
    and can fail on its own terms. The two are still told apart by ``isinstance``
    where that matters — a credential problem updates the connection's status,
    a rollup failure does not.
    """

    ok: bool = True
    cursor: str | None = None
    error: AppError | None = None


def start_sync(
    db: Session,
    settings: Settings,
    *,
    workspace_id: int,
    user_id: int | None,
    trigger: str = "manual",
    fetch: bool = True,
) -> SyncRun:
    """Queue a sync and return its row immediately.

    The row exists before the worker starts, so the client has something to
    poll from the first render rather than a gap where the sync is neither
    visible nor finished.
    """
    runs = SyncRunRepository(db)
    # Clear anything abandoned before deciding a sync is already running.
    # Start-up is not the only moment an orphaned row matters: without this, a
    # worker killed mid-run blocks Sync now until the server is restarted, and
    # restarting to clear a stuck row is not a thing to ask a user to do.
    reclaim_interrupted_runs(db)
    if runs.active(workspace_id) is not None:
        raise SyncAlreadyRunningError

    connection = ShopifyConnectionRepository(db).get(workspace_id)
    if connection is None:
        # A store configured only in .env has no row for synced data to
        # reference. Adopting it writes the credential encrypted, once.
        connection = shopify_service.adopt_env_credential(
            db, settings, workspace_id=workspace_id, user_id=user_id
        )
    if connection is None or connection.status == "disconnected":
        raise NotConnectedError

    # Resume from wherever the previous run stopped, so a partial sync
    # re-fetches only the pages it missed — unless that cursor has aged out.
    previous = runs.latest(workspace_id)
    resume_orders = _resumable_cursor(previous, connection.order_lookback_days)

    try:
        run = runs.add(
            SyncRun(
                workspace_id=workspace_id,
                connection_id=connection.id,
                trigger=trigger,
                status="queued",
                stage="queued",
                started_at=utcnow(),
                triggered_by=user_id,
                cursor_orders=resume_orders,
            )
        )
        # Both the flush inside `add` and this commit can raise it: the flush is
        # where SQLite notices on the same connection, the commit where a
        # competing transaction's insert becomes visible. Catching only one left
        # the other escaping as a 500.
        db.commit()
    except IntegrityError:
        # `uq_sync_runs_one_live_per_workspace` fired: another caller inserted a
        # live run between our `runs.active()` check above and this commit. The
        # check is not atomic and cannot be made so in application code, so the
        # database is where the decision is settled and this is that answer
        # arriving. The outcome the caller sees is the same one the check would
        # have produced had it won the race.
        db.rollback()
        log.info("sync start lost the race for workspace=%s — one is already live", workspace_id)
        raise SyncAlreadyRunningError from None

    run_id = run.id
    runner.submit(
        lambda: run_sync_job(settings, run_id=run_id, workspace_id=workspace_id, fetch=fetch),
        name=f"shopify-sync-{run_id}",
    )
    log.info("sync queued run=%s workspace=%s trigger=%s", run_id, workspace_id, trigger)
    return run


def _resumable_cursor(previous: SyncRun | None, lookback_days: int) -> str | None:
    """The previous run's cursor, if following it can still reach the present.

    **Why a cursor expires.** Shopify's ``page_info`` encodes the filters that
    produced it, including ``created_at_min``. Resuming one therefore walks the
    result set *as it was when that cursor was first issued* — orders created
    afterwards were never in that sequence, so no amount of paging reaches them.
    `_since` documents the production symptom: a cursor carrying
    ``created_at_min: 2026-04-30`` from a first sync eight days earlier, and
    every resumed run walking that old window while the newest 30 hours stayed
    unfetched.

    A cursor younger than the lookback window is still worth following: the
    window it encodes overlaps the one being asked for now, so resuming saves
    re-fetching pages that already committed. Older than that and the two
    windows have stopped overlapping — the cursor can only walk history the
    next full sync would cover anyway, so it is discarded and the run starts
    fresh at ``now - lookback``.

    Discarding costs a re-fetch. Keeping costs never catching up, which is
    silent, and the sync reports success while the dashboard stays behind.
    """
    if previous is None or not previous.cursor_orders:
        return None

    minted = _as_utc(previous.started_at)
    if minted is None:  # pragma: no cover - started_at is not nullable
        return None

    if utcnow() - minted > timedelta(days=lookback_days):
        log.info(
            "discarding sync cursor from %s: older than the %s-day lookback, "
            "so it cannot reach the present",
            minted.isoformat(),
            lookback_days,
        )
        return None
    return previous.cursor_orders


def retry_sync(
    db: Session,
    settings: Settings,
    *,
    workspace_id: int,
    user_id: int | None,
) -> SyncRun:
    """Repeat whichever stage of the last run actually failed.

    Two failures, two answers:

    * **The Shopify pull stopped.** A normal sync, which already resumes
      from the stored cursor rather than restarting — so the pages that
      landed are kept and only the missing ones are fetched.
    * **The pull worked and the recompute did not** (``rollup_failed``).
      Every order is already in the database. Fetching them again would be
      minutes of Shopify requests to arrive back exactly where we are, so
      this recomputes and nothing else.

    Either way the imported sheet is untouched and nothing needs uploading
    again: an import states which SKUs matter, and that has not changed
    because a sync failed.
    """
    previous = SyncRunRepository(db).latest(workspace_id)
    # Nothing failed, so there is nothing to repeat. Without this a retry ran a
    # full sync — 3,329 orders and a rollup rebuild, in the observed case — for a
    # run that had already succeeded. The UI only offers the control on a
    # failure, so this closes the path rather than changing what a user sees.
    if previous is not None and previous.result == "success":
        raise NothingToRetryError
    recompute_only = previous is not None and previous.error_code == ROLLUP_FAILED
    run = start_sync(
        db,
        settings,
        workspace_id=workspace_id,
        user_id=user_id,
        trigger="retry",
        fetch=not recompute_only,
    )
    log.info(
        "retry run=%s workspace=%s recompute_only=%s",
        run.id,
        workspace_id,
        recompute_only,
    )
    return run


#: How long a run may go without writing before start-up treats it as orphaned.
#:
#: The old reclaim had no such window: it closed out *every* queued or running
#: row on the assumption that this process is the only one, so nothing running
#: when it last stopped can still be running. That is false the moment a second
#: process exists — and one always does under `uvicorn --reload`, which restarts
#: on every file save. A live sync in another process was being stamped
#: `sync_interrupted` while it carried on working, which is why the production
#: database showed five consecutive "interrupted" runs that had in fact been
#: progressing normally.
#:
#: A sync commits after every page (250 orders, well under a second at the
#: measured rate), so five minutes of silence is many orders of magnitude beyond
#: normal and means the worker really is gone.
STALE_RUN_AFTER = timedelta(minutes=5)


def _last_write(run: SyncRun) -> datetime:
    """When this run last touched the database.

    ``updated_at`` is typed optional, and a row that has never been updated has
    nothing to prove it is alive — so it falls back to ``started_at``, which is
    always set, rather than being treated as fresh forever.
    """
    return _as_utc(run.updated_at) or _as_utc(run.started_at) or datetime.min.replace(tzinfo=UTC)


def reclaim_interrupted_runs(db: Session) -> int:
    """Close out runs abandoned by a process that stopped.

    Left alone those rows stay ``running`` forever and block every future sync
    with "a sync is already running" — a deadlock the user cannot clear from the
    UI. But only rows that have gone quiet are reclaimed: a run still committing
    pages belongs to a worker that is alive, in this process or another, and
    marking it interrupted would corrupt the record of a sync that is working.

    The cursors are deliberately preserved: the next run resumes from where
    the interrupted one stopped rather than re-fetching pages that already
    committed.
    """
    cutoff = utcnow() - STALE_RUN_AFTER
    candidates = list(db.scalars(select(SyncRun).where(SyncRun.status.in_(("queued", "running")))))
    # Compared in Python rather than SQL: `updated_at` comes back naive from
    # SQLite, and a naive/aware comparison in a WHERE clause silently matches
    # nothing on some drivers instead of raising.
    stale = [run for run in candidates if _last_write(run) < cutoff]
    if len(candidates) != len(stale):
        log.info(
            "left %s live sync run(s) alone; reclaiming %s",
            len(candidates) - len(stale),
            len(stale),
        )
    for run in stale:
        run.status = "finished"
        run.stage = "done"
        # 'partial' when rows landed, because they did — the counts are real
        # and the resume cursor is valid.
        run.result = "partial" if run.orders_synced else "failed"
        run.error_code = "sync_interrupted"
        run.error_detail = (
            "The sync stopped without finishing — the server restarted, or the worker "
            "was killed. The next sync resumes from where this one stopped."
        )
        run.finished_at = utcnow()
    if stale:
        db.commit()
        log.warning("reclaimed %s interrupted sync run(s)", len(stale))
    return len(stale)


def run_sync_job(
    settings: Settings,
    *,
    run_id: int,
    workspace_id: int,
    fetch: bool = True,
) -> None:
    """The worker-thread entry point. Owns its session start to finish.

    ``fetch=False`` skips the Shopify pull and goes straight to the rollup.
    It is how a retry repeats only what failed: when the orders arrived and
    the recompute did not, they are already in the database, and asking
    Shopify for them again would be minutes of work to arrive back where we
    already are. See :func:`retry_sync`.
    """
    factory = get_session_factory()
    with factory() as db:
        run = SyncRunRepository(db).get(workspace_id, run_id)
        if run is None:  # pragma: no cover - only if the row was deleted
            log.warning("sync run %s vanished before it started", run_id)
            return

        connection = ShopifyConnectionRepository(db).get(workspace_id)
        if connection is None:
            _finish(db, run, result="failed", code="shopify_not_connected", detail="No connection.")
            return

        try:
            token = crypto.decrypt(settings, connection.access_token_encrypted)
        except AppError as exc:
            _finish(db, run, result="failed", code=exc.code, detail=exc.message)
            return

        client = ShopifyClient(settings=settings, shop_domain=connection.shop_domain, token=token)

        run.status = "running"
        db.commit()
        activity.record(
            db,
            workspace_id=workspace_id,
            run_id=run.id,
            step="sync_started",
            state="started",
            detail=(
                "Recomputing from orders already downloaded"
                if not fetch
                else f"Pulling orders from {connection.shop_domain}"
            ),
        )

        if fetch:
            orders = _sync_orders(
                db, client, run, connection.id, workspace_id, connection.order_lookback_days
            )
        else:
            # Recompute-only retry. The orders this run reports are the ones
            # already downloaded, so the figures it produces cover them —
            # `_refresh_rollup` needs a non-zero count to know there is work.
            run.orders_synced = OrderRepository(db).count(workspace_id)
            run.orders_pct = 100
            db.commit()
            orders = StageOutcome(ok=True)
            log.info("run %s is a recompute-only retry; skipping the fetch", run.id)

        # Plan §4.1: the rollup is refreshed over a bounded window so the cost
        # stays flat as order history accumulates.
        #
        # **Before the run is called successful, not after.** Orders in the
        # database that the rollup has not seen are orders no figure on any
        # screen reflects, so a run that fetched them and stopped there has not
        # finished the job it was started for. It used to be recorded as
        # success and the failure logged, which left the Dashboard showing
        # yesterday's numbers under a green badge and a banner asking the user
        # to press Recompute — a button for a repair the sync should have made
        # itself.
        activity.record(
            db,
            workspace_id=workspace_id,
            run_id=run.id,
            step="sync_completed",
            state="ok" if orders.ok else "failed",
            detail=(
                f"{run.orders_synced:,} orders, {run.line_items_synced:,} line items"
                if orders.ok
                else (orders.error.message if orders.error else None)
            ),
        )

        rollup = _refresh_rollup(db, run, workspace_id)

        _record_outcome(db, run, connection_id=connection.id, orders=orders, rollup=rollup)
        activity.record(
            db,
            workspace_id=workspace_id,
            run_id=run.id,
            step="workflow_finished",
            state="ok" if run.result == "success" else "failed",
            detail=run.error_detail or f"Final status: {run.result}",
        )

        # Record where the store had got to, so every screen can say whether the
        # run actually caught up. The client is already built and authenticated,
        # so this is one extra request at the end of a sync rather than one on
        # every page load — and it is the moment the answer is most worth having.
        try:
            check_freshness(db, settings, workspace_id=workspace_id)
        except (ShopifyError, SQLAlchemyError):
            log.info("could not record store freshness after sync %s", run.id)


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def _sync_orders(
    db: Session,
    client: ShopifyClient,
    run: SyncRun,
    connection_id: int,
    workspace_id: int,
    lookback_days: int,
) -> StageOutcome:
    run.stage = "orders"
    db.commit()

    since = _since(db, run, workspace_id, lookback_days)
    total = client.count("orders/count.json", {"status": "any", "created_at_min": since})

    #: Shopify order ids this run has already counted. Cursor pagination re-serves
    #: an order whose position shifted mid-walk (see `_write_order_page`), and
    #: counting each sighting made `orders_synced` and `orders_pct` climb past the
    #: real totals — which is what made a sync that was repeating work look like a
    #: sync that was getting somewhere. Scoped to the run, which is also the scope
    #: of `run.orders_synced`, and bounded by the lookback window.
    counted: set[int] = set()
    #: The same, for line items — `line_items_synced` inflated identically.
    counted_lines: set[int] = set()

    try:
        for page in client.orders(since=since, start_cursor=run.cursor_orders):
            _write_order_page(db, page, connection_id, workspace_id, run, counted, counted_lines)
            run.orders_pct = _percent(len(counted), total)
            run.cursor_orders = page.next_cursor
            db.commit()
    except ShopifyError as exc:
        log.warning("order sync stopped: %s", exc.code)
        return StageOutcome(ok=False, cursor=run.cursor_orders, error=exc)

    run.cursor_orders = None
    run.orders_pct = 100
    db.commit()
    return StageOutcome(ok=True)


#: How far back an incremental sync reaches beyond the newest order it holds.
#: Orders are not created in a strictly increasing order once time zones, delayed
#: webhooks and back-dated draft orders are involved, so the window overlaps
#: rather than butting up exactly against the last one. Re-fetched orders upsert.
INCREMENTAL_OVERLAP = timedelta(hours=2)


def _since(db: Session, run: SyncRun, workspace_id: int, lookback_days: int) -> str:
    """The ``created_at_min`` this run should ask Shopify for.

    **Why this is not simply ``now - lookback``.** Shopify's cursor encodes the
    filters that produced it, including ``created_at_min``, so resuming a stored
    cursor walks the result set *as it was when that cursor was first issued*.
    Orders created afterwards were never in that sequence. On the production store
    the cursor carried ``created_at_min: 2026-04-30`` from the first sync eight
    days earlier, and every resumed run walked that old window — so a completed
    catch-up still left the newest 30 hours unfetched, and no amount of syncing
    would ever reach the present.

    So: a run that is resuming keeps the full lookback, because it is finishing a
    window it is already partway through. A run starting fresh after a successful
    one anchors on the newest order it already holds, which makes catching up cost
    O(new orders) instead of O(the whole window) — 1,523 pages at this store — and
    guarantees the request reaches *now*, because it is computed at run time.

    After anything other than a success the full lookback is used, so a gap left
    mid-window by a failed run is backfilled rather than skipped over.
    """
    floor = (datetime.now(UTC) - timedelta(days=lookback_days)).replace(microsecond=0)

    if run.cursor_orders:
        return floor.isoformat()

    previous = SyncRunRepository(db).latest_success(workspace_id)
    if previous is None:
        return floor.isoformat()

    newest = _as_utc(OrderRepository(db).latest_created_at(workspace_id))
    if newest is None:
        return floor.isoformat()

    anchor = max(floor, (newest - INCREMENTAL_OVERLAP).replace(microsecond=0))
    log.info("incremental sync from %s (newest order %s)", anchor.isoformat(), newest)
    return anchor.isoformat()


def _write_order_page(
    db: Session,
    page: Page,
    connection_id: int,
    workspace_id: int,
    run: SyncRun,
    counted: set[int],
    counted_lines: set[int],
) -> None:
    orders = OrderRepository(db)
    now = utcnow()

    order_ids = [int(o["id"]) for o in page.items if o.get("id") is not None]
    # Seeded from the database and then *added to* as rows are created, so this
    # page's write is idempotent in itself and not merely against what was
    # already committed.
    #
    # Without it, an order arriving twice before the flush looked new both times
    # and inserted a second copy, dying on
    # `UNIQUE constraint failed: orders.workspace_id, orders.shopify_order_id`.
    # Two ways that happens: a second writer touching the same run (observed —
    # a run executed by the worker thread and by a script at once), and Shopify
    # returning an order twice, which cursor pagination permits because the walk
    # is not a snapshot and an order updated mid-walk shifts position.
    known: dict[int, Order] = dict(orders.orders_by_shopify_id(workspace_id, order_ids))

    line_payloads: list[tuple[Order, dict[str, Any]]] = []

    for payload in page.items:
        shopify_id = payload.get("id")
        if shopify_id is None:
            continue
        shopify_id = int(shopify_id)

        order = known.get(shopify_id)
        if order is None:
            order = orders.add_order(
                Order(
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    shopify_order_id=shopify_id,
                    synced_at=now,
                )
            )
            known[shopify_id] = order
        order.order_number = _text(payload.get("name") or payload.get("order_number"), 64)
        order.created_at_shopify = _timestamp(payload.get("created_at"))
        order.processed_at = _timestamp(payload.get("processed_at")) or order.created_at_shopify
        order.cancelled_at = _timestamp(payload.get("cancelled_at"))
        order.financial_status = _text(payload.get("financial_status"), 32)
        order.fulfillment_status = _text(payload.get("fulfillment_status"), 32)
        order.currency = _text(payload.get("currency"), 8)
        order.total_price_paise = _paise(payload.get("total_price"))
        order.synced_at = now
        if shopify_id not in counted:
            counted.add(shopify_id)
            run.orders_synced += 1

        for line in payload.get("line_items") or []:
            line_payloads.append((order, line))

    db.flush()
    _write_line_items(db, orders, line_payloads, workspace_id, run, counted_lines)


def _write_line_items(
    db: Session,
    orders: OrderRepository,
    payloads: list[tuple[Order, dict[str, Any]]],
    workspace_id: int,
    run: SyncRun,
    counted: set[int],
) -> None:
    line_ids = [int(line["id"]) for _, line in payloads if line.get("id") is not None]
    # Same reasoning as the orders above: a line arriving twice in one flush must
    # update the row created moments ago, not insert a second one.
    known: dict[int, OrderLineItem] = dict(orders.line_items_by_shopify_id(workspace_id, line_ids))

    for order, payload in payloads:
        shopify_id = payload.get("id")
        if shopify_id is None:
            continue
        shopify_id = int(shopify_id)

        item = known.get(shopify_id)
        if item is None:
            item = orders.add_line_item(
                OrderLineItem(
                    workspace_id=workspace_id,
                    order_id=order.id,
                    shopify_line_item_id=shopify_id,
                )
            )
            known[shopify_id] = item
        sku = _text(payload.get("sku"), 120)
        item.order_id = order.id
        item.shopify_variant_id = _int_or_none(payload.get("variant_id"))
        item.sku_at_sale = sku
        item.sku_normalized = normalize_sku(sku or "")
        item.title = _text(payload.get("title"), 500)
        item.quantity = _int_or_none(payload.get("quantity")) or 0
        item.price_paise = _paise(payload.get("price"))
        item.total_discount_paise = _paise(payload.get("total_discount"))
        if shopify_id not in counted:
            counted.add(shopify_id)
            run.line_items_synced += 1


# ---------------------------------------------------------------------------
# outcome
# ---------------------------------------------------------------------------


#: The error code a rollup failure is recorded under. Named because the
#: retry branches on it: this is the failure that needs no Shopify call.
ROLLUP_FAILED = "rollup_failed"


class RollupFailedError(AppError):
    """The orders arrived; recomputing the figures from them did not.

    A distinct code because the user's next step is distinct. Nothing needs
    re-fetching from Shopify — the rows are already here — so the answer is to
    recompute, which the next sync will attempt again on its own.
    """

    code = ROLLUP_FAILED
    status_code = 500
    message = "Orders synced, but the figures could not be recomputed from them."
    next_step = "The next sync will try again. If it keeps failing, check the server logs."


def _refresh_rollup(db: Session, run: SyncRun, workspace_id: int) -> StageOutcome:
    """Recompute the figures the orders just landed feed into.

    Nothing to recompute when nothing arrived: with no new orders the rollup
    already covers everything in the database, and rebuilding it would be work
    with no result. That is reported as ok, because it is.
    """
    if not run.orders_synced:
        return StageOutcome(ok=True)
    activity.record(
        db,
        workspace_id=workspace_id,
        run_id=run.id,
        step="recompute_started",
        state="started",
    )
    try:
        result = metrics.refresh_recent(db, workspace_id=workspace_id)
    except SQLAlchemyError:
        log.warning("could not refresh metrics after sync %s", run.id, exc_info=True)
        activity.record(
            db,
            workspace_id=workspace_id,
            run_id=run.id,
            step="recompute_failed",
            state="failed",
            detail="The figures could not be recomputed from the orders that arrived.",
        )
        return StageOutcome(ok=False, error=RollupFailedError())
    activity.record(
        db,
        workspace_id=workspace_id,
        run_id=run.id,
        step="recompute_completed",
        detail=f"{result.rows_written:,} daily rows over {result.days_covered} day(s)",
    )
    log.info(
        "rollup refreshed after sync %s: %s rows over %s day(s) in %s ms",
        run.id,
        result.rows_written,
        result.days_covered,
        result.duration_ms,
    )
    return StageOutcome(ok=True)


def _record_outcome(
    db: Session,
    run: SyncRun,
    *,
    connection_id: int,
    orders: StageOutcome,
    rollup: StageOutcome,
) -> None:
    # Order matters: a Shopify failure is reported ahead of a rollup one,
    # because it is the earlier and more actionable of the two.
    failures = [s for s in (orders, rollup) if not s.ok]

    if not failures:
        _finish(db, run, result="success")
        return

    # Something landed, so this is partial rather than failed — the distinction
    # the amber badge and the resume cursors both depend on.
    landed = run.orders_synced
    first = failures[0].error
    result = "partial" if landed else "failed"

    # A credential problem is a property of the connection, not of this run, so
    # it is recorded where the sidebar reads it (§4).
    #
    # The two are told apart deliberately. A missing scope is not an expired
    # token, and labelling it one sends the user to regenerate a token that was
    # never the problem — a dead end, because the fix is granting the scope in
    # the Shopify admin. This store is exactly that case: the token
    # authenticates and syncs orders fine, and only the product stage 403s.
    if isinstance(first, ShopifyScopeError):
        connection = ShopifyConnectionRepository(db).get(run.workspace_id)
        if connection is not None and connection.id == connection_id:
            connection.status = "missing_scopes"
    elif isinstance(first, ShopifyAuthError):
        connection = ShopifyConnectionRepository(db).get(run.workspace_id)
        if connection is not None and connection.id == connection_id:
            connection.status = "token_expired"

    _finish(
        db,
        run,
        result=result,
        code=first.code if first else "shopify_error",
        detail=first.message if first else "Sync did not complete.",
        retry_after=(first.detail or {}).get("retry_after_seconds") if first else None,
    )


def _finish(
    db: Session,
    run: SyncRun,
    *,
    result: str,
    code: str | None = None,
    detail: str | None = None,
    retry_after: int | None = None,
) -> None:
    run.status = "finished"
    run.stage = "done"
    run.result = result
    run.error_code = code
    run.error_detail = detail
    run.retry_after_seconds = retry_after
    run.finished_at = utcnow()
    db.commit()
    log.info(
        "sync finished run=%s result=%s orders=%s lines=%s",
        run.id,
        result,
        run.orders_synced,
        run.line_items_synced,
    )


# ---------------------------------------------------------------------------
# coercion
# ---------------------------------------------------------------------------


def _percent(seen: int, total: int | None) -> int:
    """Progress as a percentage, capped below 100 while work remains.

    Reporting 100 before the stage is done makes the bar sit full while the
    user waits, which reads as a hang. 99 is honest.
    """
    if not total or total <= 0:
        return 50 if seen else 0
    return min(99, int(seen * 100 / total))


def _text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _paise(value: object) -> int | None:
    """Shopify sends money as a decimal string. Store exact paise (plan §4.5)."""
    if value is None:
        return None
    try:
        return round(float(str(value)) * 100)
    except (TypeError, ValueError):
        return None


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Freshness:
    """How far behind the live store the database is.

    ``synced_through`` is ours; ``store_latest`` is Shopify's. The gap between
    them is the only honest measure of staleness — the previous one compared the
    rollup against the orders table, which says whether the *derived* layer is
    current and nothing at all about whether the orders themselves are.
    """

    synced_through: datetime | None
    store_latest: datetime | None
    checked_at: datetime | None
    #: True when the store has orders newer than anything synced, by more than
    #: the tolerance below. None when Shopify could not be reached.
    behind: bool | None
    behind_seconds: int | None

    @property
    def behind_hours(self) -> float | None:
        return round(self.behind_seconds / 3600, 1) if self.behind_seconds else None


#: How far behind counts as behind. A sync takes minutes and orders arrive
#: continuously, so a few minutes' lag is the normal steady state rather than a
#: problem worth a banner.
FRESHNESS_TOLERANCE = timedelta(minutes=15)


def _as_utc(value: datetime | None) -> datetime | None:
    """Shopify sends offsets; SQLite hands back naive datetimes. Compare in UTC."""
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def freshness_from(
    connection: ShopifyConnection | None, synced_through: datetime | None
) -> Freshness:
    """Compute freshness from what is already stored. No network."""
    store_latest = _as_utc(connection.store_latest_order_at if connection else None)
    ours = _as_utc(synced_through)

    behind: bool | None = None
    gap: int | None = None
    if store_latest is not None:
        if ours is None:
            behind, gap = True, None
        else:
            delta = store_latest - ours
            behind = delta > FRESHNESS_TOLERANCE
            gap = int(delta.total_seconds()) if delta.total_seconds() > 0 else 0

    return Freshness(
        synced_through=ours,
        store_latest=store_latest,
        checked_at=_as_utc(connection.freshness_checked_at if connection else None),
        behind=behind,
        behind_seconds=gap,
    )


def check_freshness(db: Session, settings: Settings, *, workspace_id: int) -> Freshness:
    """Ask Shopify for its newest order, record it, and report the gap.

    One request. Called deliberately — from the Shopify page, and after a sync —
    never on a dashboard read: spending a Shopify request to render a caption
    would put the rate limit in the path of every page load.

    A store that cannot be reached leaves the stored value alone and reports
    ``behind=None``: "we do not know" is a different answer from "we are current",
    and reporting the second when the first is true is how a stale figure gets
    presented as a fresh one.
    """
    connection = ShopifyConnectionRepository(db).get(workspace_id)
    orders = OrderRepository(db)
    synced_through = orders.latest_processed_at(workspace_id)

    credential = shopify_service.resolve_credential(db, settings, workspace_id=workspace_id)
    if credential is None:
        return freshness_from(connection, synced_through)

    client = ShopifyClient(
        settings=settings, shop_domain=credential.shop_domain, token=credential.token
    )
    raw = client.newest_order_at()
    if raw is None:
        return freshness_from(connection, synced_through)

    try:
        store_latest = datetime.fromisoformat(raw)
    except ValueError:
        log.warning("could not parse Shopify's newest order timestamp")
        return freshness_from(connection, synced_through)

    if connection is not None:
        connection.store_latest_order_at = _as_utc(store_latest)
        connection.freshness_checked_at = utcnow()
        try:
            db.commit()
        except SQLAlchemyError:
            # A sync in flight holds SQLite's write lock in bursts, so this
            # small write can lose the race. Failing to *record* the answer is
            # not a reason to withhold it: the value was read from Shopify a
            # moment ago and is what the caller asked for. Recording it again is
            # what the next check is for.
            db.rollback()
            log.info("could not record store freshness; reporting it without storing")
            return _freshness_of(store_latest, synced_through, checked_at=utcnow())
        return freshness_from(connection, synced_through)

    # An .env-only store has no row to record against, so the answer is computed
    # rather than stored. It is still the real gap.
    return _freshness_of(store_latest, synced_through, checked_at=utcnow())


def _freshness_of(
    store_latest: datetime | None, synced_through: datetime | None, *, checked_at: datetime
) -> Freshness:
    """The gap, computed from two timestamps and stored nowhere."""
    ours = _as_utc(synced_through)
    latest = _as_utc(store_latest)
    delta = (latest - ours) if (latest and ours) else None
    return Freshness(
        synced_through=ours,
        store_latest=latest,
        checked_at=checked_at,
        behind=(delta > FRESHNESS_TOLERANCE) if delta is not None else (latest is not None),
        behind_seconds=int(delta.total_seconds()) if delta and delta.total_seconds() > 0 else 0,
    )
