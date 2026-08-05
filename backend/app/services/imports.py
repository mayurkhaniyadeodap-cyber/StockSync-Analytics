"""Running an import: parse, then reconcile the result into current stock."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.calc import sales_pct
from app.core.errors import AppError
from app.db.session import get_session_factory
from app.models import (
    COMPLAINT_COLUMNS,
    ImportBatch,
    InventoryItem,
    SkuDailyComplaint,
    User,
    utcnow,
)
from app.repositories import ImportBatchRepository, InventoryItemRepository
from app.repositories.analytics import SkuFactRepository
from app.services import analytics, import_files, metrics
from app.services.import_files import ImportFileError, ParseResult

log = logging.getLogger(__name__)

METHOD_BY_SUFFIX = {"csv": "csv_upload", "xlsx": "excel_upload", "xlsm": "excel_upload"}


class FileTooLargeError(AppError):
    code = "file_too_large"
    status_code = 413
    message = "That file is larger than the upload limit."
    next_step = "Split it into smaller files, or raise STOCKSYNC_MAX_UPLOAD_MB."


class NoUsableRowsError(AppError):
    """The file parsed, but not one row could be stored."""

    code = "no_usable_rows"
    status_code = 422
    message = "None of the rows in that file could be imported."
    next_step = "Check the SKU and Total Qty. columns have values, then upload again."


@dataclass(frozen=True)
class AnalysisSummary:
    """What the import means once Shopify sales are matched onto it.

    Computed at the end of the import so the answer arrives with the upload
    rather than on the next dashboard load. Every figure is read back out of the
    database through the same repository the dashboard uses — a report of the
    saved state, not a second calculation of it.
    """

    skus_analyzed: int = 0
    skus_matched: int = 0
    skus_unmatched: int = 0
    shopify_sales: int = 0
    shopify_sales_pct: float = 0.0
    total_complaints: int = 0


@dataclass(frozen=True)
class ImportOutcome:
    batch: ImportBatch
    parse: ParseResult
    created: int
    updated: int
    #: SKUs the previous dataset held that this sheet does not. They are gone,
    #: not hidden, so the number is reported rather than left to be noticed.
    removed: int = 0
    # Frozen, so an instance is an immutable default and needs no factory.
    analysis: AnalysisSummary = AnalysisSummary()


def _method_for(filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return METHOD_BY_SUFFIX.get(suffix, "csv_upload")


def run_file_import(
    db: Session,
    *,
    workspace_id: int,
    user: User,
    filename: str,
    raw: bytes,
    max_bytes: int,
    method: str | None = None,
) -> ImportOutcome:
    """Import a sheet and return what happened.

    The batch row is written whether or not the import succeeds — a failed
    import that leaves no trace is the thing Import History exists to prevent.
    Because the failure path has to survive, it commits the batch itself before
    re-raising; the success path leaves the transaction open for the route.

    ``method`` overrides how History records the source. It exists because a
    sheet fetched from a URL is this same import — parsing, validation, upsert
    and batch record identical — differing only in how the bytes arrived, which
    the filename cannot express.
    """
    if len(raw) > max_bytes:
        raise FileTooLargeError(
            detail={"size_bytes": len(raw), "limit_bytes": max_bytes},
        )

    batches = ImportBatchRepository(db)
    started = utcnow()

    batch = batches.add(
        ImportBatch(
            workspace_id=workspace_id,
            method=method or _method_for(filename),
            origin_filename=filename[:255],
            status="reading",
            started_at=started,
            triggered_by=user.id,
        )
    )

    try:
        parse = import_files.parse_inventory_file(filename, raw)
    except ImportFileError as exc:
        _fail(db, batch, code=exc.code, detail=exc.message)
        raise

    batch.status = "saving"
    batch.rows_read = parse.rows_read
    batch.rows_rejected = len(parse.rejected)
    batch.rows_merged = parse.rows_merged
    batch.rows_flagged = len(parse.rejected) + parse.rows_merged

    if not parse.rows:
        _fail(
            db,
            batch,
            code=NoUsableRowsError.code,
            detail=NoUsableRowsError.message,
            rows_imported=0,
        )
        raise NoUsableRowsError(
            detail={
                "rows_read": parse.rows_read,
                "rows_rejected": len(parse.rejected),
                "batch_id": batch.id,
            }
        )

    _reconcile_quantities(parse, batch_id=batch.id)
    created, updated, removed = _replace_items(db, workspace_id, batch, parse, now=started)
    _replace_dated_complaints(db, workspace_id, parse, now=started)

    batch.rows_imported = len(parse.rows)
    # 'partial' is not a soft failure — rows landed. It says some did not, which
    # is what the amber badge in Import History means (§8.8).
    batch.status = "partial" if parse.rejected else "complete"
    batch.finished_at = utcnow()

    log.info(
        "import batch=%s status=%s read=%s imported=%s rejected=%s merged=%s",
        batch.id,
        batch.status,
        batch.rows_read,
        batch.rows_imported,
        batch.rows_rejected,
        batch.rows_merged,
    )
    return ImportOutcome(
        batch=batch,
        parse=parse,
        created=created,
        updated=updated,
        removed=removed,
        analysis=_analyze(db, workspace_id=workspace_id, days=analytics.DEFAULT_RANGE),
    )


def refresh_rollup_job(workspace_id: int) -> None:
    """Rebuild the rollup if the sync has moved past it. Runs on the worker.

    Owns its own session, like every other job: it runs on a thread, so it
    cannot borrow the request's. That is also what keeps the import to one
    transaction — ``metrics.refresh`` commits, and doing that inside the import
    would end the route's transaction halfway through and break the rollback
    the failure path depends on.
    """
    factory = get_session_factory()
    try:
        with factory() as db:
            if not metrics.is_stale(db, workspace_id=workspace_id):
                return
            result = metrics.refresh_recent(db, workspace_id=workspace_id)
            log.info(
                "rollup refreshed after an import: %s rows over %s day(s) in %s ms",
                result.rows_written,
                result.days_covered,
                result.duration_ms,
            )
    except SQLAlchemyError:  # a worker thread must not die silently
        log.warning("could not refresh the rollup after an import", exc_info=True)


def _analyze(db: Session, *, workspace_id: int, days: int) -> AnalysisSummary:
    """Match the imported SKUs against Shopify sales and report the result.

    **Reads only.** The rollup rebuild it used to do inline is now
    ``refresh_rollup_job`` on the existing worker, for two reasons: rebuilding
    474,191 rows from 2.2 million line items is not something to hold an upload
    open for, and ``metrics.refresh`` commits — which inside the import would
    take the transaction boundary away from the route.

    The consequence is that these figures are computed against the rollup as it
    stands. If a sync landed since the last rebuild the summary can be a little
    behind for the seconds the job takes; the dashboard the user lands on next
    is already correct. That is the trade for not blocking the upload, and it is
    the one the requirement asks for.
    """
    # The session runs with autoflush off, so the rows just upserted are still
    # pending and a query would not see them. Flush, not commit: the route owns
    # the transaction.
    db.flush()

    since, until = analytics.window(days)
    repository = SkuFactRepository(db)
    facts = repository.facts(workspace_id, since=since, until=until)
    all_units = repository.window_units(workspace_id, since=since, until=until)

    matched = sum(1 for fact in facts if fact.shopify_sales > 0)
    sales = sum(fact.shopify_sales for fact in facts)
    complaints = sum(fact.total_complaints for fact in facts)

    return AnalysisSummary(
        skus_analyzed=len(facts),
        skus_matched=matched,
        skus_unmatched=len(facts) - matched,
        shopify_sales=sales,
        shopify_sales_pct=sales_pct(sales, all_units),
        total_complaints=complaints,
    )


def _fail(
    db: Session,
    batch: ImportBatch,
    *,
    code: str,
    detail: str,
    rows_imported: int = 0,
) -> None:
    """Record the failure and commit it, so it survives the exception."""
    batch.status = "failed"
    batch.error_code = code
    batch.error_detail = detail
    batch.rows_imported = rows_imported
    batch.finished_at = utcnow()
    db.commit()
    log.warning("import batch=%s failed code=%s", batch.id, code)


def _reconcile_quantities(parse: ParseResult, *, batch_id: int) -> int:
    """Make sure the two places a row states its quantity agree.

    ``quantity_on_hand`` and ``total_qty`` are written from one row and read by
    different screens — the dashboard card sums the first, Analytics sums the
    second. Both parsers derive them from the same cell, so they cannot diverge
    today; this exists because *the database contains rows where they do*,
    written by an earlier version, and the disagreement was invisible until two
    screens were compared by hand.

    The sheet's own ``Total Qty`` is treated as authoritative when they differ:
    it is the more specific statement, and the resolved quantity falls back to
    it anyway when no other stock column exists.

    Returns how many rows had to be corrected, and says so in the log rather
    than accepting a contradiction quietly.
    """
    corrected = 0
    for index, row in enumerate(parse.rows):
        stated = row.counts.get("total_qty")
        if stated is None or stated == row.quantity:
            continue
        log.warning(
            "import batch=%s sku=%s quantity %s disagrees with Total Qty %s; using Total Qty",
            batch_id,
            row.sku,
            row.quantity,
            stated,
        )
        # ParsedRow is frozen, so this is a replacement rather than an edit.
        parse.rows[index] = replace(row, quantity=stated)
        corrected += 1

    if corrected:
        log.warning(
            "import batch=%s reconciled %s row(s) whose quantity disagreed with Total Qty",
            batch_id,
            corrected,
        )
    return corrected


def _replace_dated_complaints(
    db: Session,
    workspace_id: int,
    parse: ParseResult,
    *,
    now: datetime,
) -> int:
    """Rewrite the workspace's dated complaint rows from a complaint export.

    **Only a complaint export may touch this table**, and when it does it
    replaces every row for the workspace: it is one file stating the whole dated
    complaint record, so a SKU it does not mention has no dated complaints
    according to it.

    A stock sheet returns immediately. It used to clear the table too, on the
    reasoning that an aggregated sheet's totals would otherwise be counted twice
    — once dated and once not. That reasoning is sound for one SKU and wrong for
    the workspace: the per-SKU rule in ``repositories.complaints`` already
    prevents double-counting, because a SKU with dated rows never has its
    aggregated columns read. Clearing the table was therefore not preventing a
    double count; it was deleting the other file's data. On the live workspace
    the two formats alternated for 24 imports and each erased the other, which is
    why eleven successful complaint imports left no dated rows at all.
    """
    if parse.sheet_format != import_files.COMPLAINTS:
        return 0

    db.execute(delete(SkuDailyComplaint).where(SkuDailyComplaint.workspace_id == workspace_id))
    if not parse.rows:
        return 0

    batch_rows: list[dict[str, object]] = []
    for row in parse.rows:
        for day, by_category in row.dated_counts.items():
            total = sum(by_category.values())
            if total <= 0:
                continue
            batch_rows.append(
                {
                    "workspace_id": workspace_id,
                    "sku_normalized": row.sku_normalized,
                    "complaint_date": day,
                    "total_complaints": total,
                    "imported_at": now,
                    **{name: by_category.get(name, 0) for name, _ in COMPLAINT_COLUMNS},
                }
            )
        if len(batch_rows) >= 2000:
            db.execute(insert(SkuDailyComplaint), batch_rows)
            batch_rows = []

    if batch_rows:
        db.execute(insert(SkuDailyComplaint), batch_rows)

    written = sum(
        1 for row in parse.rows for counts in row.dated_counts.values() if sum(counts.values()) > 0
    )
    log.info(
        "dated complaints workspace=%s skus=%s rows=%s undated=%s",
        workspace_id,
        len(parse.rows),
        written,
        sum(row.undated_complaints for row in parse.rows),
    )
    return written


def _replace_items(
    db: Session,
    workspace_id: int,
    batch: ImportBatch,
    parse: ParseResult,
    *,
    now: datetime,
) -> tuple[int, int, int]:
    """Apply this sheet to the workspace's stock. Returns (new, restated, removed).

    **Replacement is scoped to what the file is actually a statement about.**
    That is the correction; the rest of this docstring is why each half is what
    it is.

    A **stock sheet** states the whole catalogue. A sheet of 309 SKUs therefore
    leaves 309 SKUs however many were there before. The alternative — writing the
    rows it was given and leaving every other SKU untouched — is defensible in
    isolation and wrong in aggregate: a 1,372 row spreadsheet followed by a 309
    row export left 1,641 SKUs on screen, 1,332 of them stale, carrying complaint
    totals from a file nobody had looked at in weeks into every figure the
    Dashboard showed.

    A **complaint export** states no such thing. It is one row per complaint for
    the SKUs that had one, and it knows nothing about stock, orders or the
    catalogue's shape. Treating it as a whole-catalogue statement is what made
    the two file types destroy each other: on the live workspace the user
    alternated the two for 24 imports and each erased the other, which is why
    ``sku_daily_complaints`` sat empty despite eleven successful complaint
    imports. So a complaint export *merges*: it updates the complaint columns of
    the SKUs it names, creates rows for SKUs not yet known so their complaints
    are visible, and leaves stock, orders and every unmentioned SKU alone.

    Deleting is safe here and nowhere else in the import: the route owns the
    transaction and does not commit until every row is written, so a file that
    fails to parse, or one whose rows are all rejected, raises before this is
    reached and leaves the previous dataset exactly as it was.
    """
    items = InventoryItemRepository(db)

    incoming = {row.sku_normalized for row in parse.rows}
    held = items.normalized_skus(workspace_id)
    created = len(incoming - held)
    updated = len(incoming & held)

    if parse.sheet_format == import_files.COMPLAINTS:
        _merge_complaint_rows(db, workspace_id, batch, parse, now=now, held=held)
        log.info(
            "import batch=%s merged complaint totals: %s SKUs in, %s new, %s restated, "
            "%s left untouched",
            batch.id,
            len(parse.rows),
            created,
            updated,
            len(held - incoming),
        )
        # Nothing was removed: a complaint export does not describe the catalogue,
        # so it cannot say a SKU has stopped existing.
        return created, updated, 0

    removed = len(held - incoming)
    items.clear(workspace_id)

    for row in parse.rows:
        items.add(
            InventoryItem(
                workspace_id=workspace_id,
                sku=row.sku,
                sku_normalized=row.sku_normalized,
                product_name=row.product_name,
                category=row.category,
                price_paise=row.price_paise,
                quantity_on_hand=row.quantity,
                quantity_imported=row.quantity,
                source_batch_id=batch.id,
                first_seen_at=now,
                last_imported_at=now,
                # The sheet's own count and complaint columns.
                **row.counts,
            )
        )

    if removed:
        log.info(
            "import batch=%s replaced the dataset: %s in, %s new, %s restated, %s removed",
            batch.id,
            len(parse.rows),
            created,
            updated,
            removed,
        )
    return created, updated, removed


def _merge_complaint_rows(
    db: Session,
    workspace_id: int,
    batch: ImportBatch,
    parse: ParseResult,
    *,
    now: datetime,
    held: set[str],
) -> None:
    """Write a complaint export's tallies without disturbing the catalogue.

    Only the complaint columns are touched on a SKU that already exists. Its
    stock, order count and descriptive fields came from the stock sheet and are
    that file's to state — overwriting them with a complaint export's zeroes is
    exactly the data loss this function exists to stop.

    A SKU the workspace has never seen still gets a row, because a complaint
    against a product nobody has imported is still a complaint the user needs to
    see. It is created with the complaint columns filled and the stock columns at
    zero, which is the truth: no stock sheet has said anything about it.
    """
    existing = {
        item.sku_normalized: item
        for item in db.scalars(
            select(InventoryItem).where(InventoryItem.workspace_id == workspace_id)
        )
    }
    complaint_fields = [attribute for attribute, _ in COMPLAINT_COLUMNS]

    for row in parse.rows:
        item = existing.get(row.sku_normalized)
        if item is None:
            db.add(
                InventoryItem(
                    workspace_id=workspace_id,
                    sku=row.sku,
                    sku_normalized=row.sku_normalized,
                    product_name=row.product_name,
                    category=row.category,
                    price_paise=row.price_paise,
                    quantity_on_hand=row.quantity,
                    quantity_imported=row.quantity,
                    source_batch_id=batch.id,
                    first_seen_at=now,
                    last_imported_at=now,
                    **row.counts,
                )
            )
            continue

        for attribute in complaint_fields:
            setattr(item, attribute, row.counts.get(attribute, 0))
        item.last_imported_at = now
        item.source_batch_id = batch.id

    # Every SKU the file did not mention has no complaints according to it, and
    # the file is the whole complaint record. Leaving their old tallies would
    # keep reporting complaints the latest export says are gone.
    for sku, item in existing.items():
        if sku in {row.sku_normalized for row in parse.rows}:
            continue
        if sku not in held:  # pragma: no cover - defensive; `held` is `existing`
            continue
        for attribute in complaint_fields:
            setattr(item, attribute, 0)
