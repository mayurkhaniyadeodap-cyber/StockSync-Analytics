"""Inventory import endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.deps import CurrentUser, DbDep, SettingsDep
from app.core.errors import AppError
from app.models import LinkedSheet
from app.repositories import (
    ImportBatchRepository,
    InventoryItemRepository,
    LinkedSheetRepository,
)
from app.schemas.imports import (
    AnalysisSummaryPayload,
    DuplicatePayload,
    GoogleSheetImportRequest,
    ImportBatchSummary,
    ImportHistoryPage,
    ImportResult,
    InventoryItemPayload,
    InventoryPage,
    InventorySummary,
    LinkedSheetList,
    LinkedSheetPayload,
    LinkSheetRequest,
    RejectedRowPayload,
    SyncAfterImportPayload,
)
from app.services import activity, google_sheets
from app.services import imports as import_service
from app.services import sheets as sheets_service
from app.services import sync as sync_service
from app.workers import runner

log = logging.getLogger(__name__)

router = APIRouter(tags=["imports"])

# The upload response is a summary, not an audit log. A file where every row is
# malformed would otherwise return one message per row.
MAX_REPORTED_ROWS = 50


def _sync_after_import(
    db: DbDep, settings: SettingsDep, user: CurrentUser, *, batch_id: int
) -> SyncAfterImportPayload:
    """Pull Shopify sales for the dataset the import just established.

    An import restates which SKUs matter. Their sales figures are only as
    current as the last sync, so leaving that to a button meant a freshly
    imported workspace showing stale sales — or none at all — until someone
    thought to press it.

    **The sync is not scoped to the imported SKUs, and cannot be.** Shopify
    serves orders by date, not by SKU, so there is nothing to narrow the fetch
    with. It does not need narrowing either: the Analytics read left-joins from
    ``inventory_items``, so only the SKUs in the latest import are ever shown,
    while ``shopify_sales_all`` keeps counting the whole store because that is
    the denominator Shopify Sales % divides by.

    Neither refusal is a failure of the import:

    * **not connected** — there is no store to fetch from, and a sheet is worth
      importing without one.
    * **already running** — a run in flight will cover these SKUs when it
      finishes, and queueing a second would be refused anyway.

    In both cases the rollup still has to be rebuilt for the rows just written,
    which a sync would otherwise have done at its own end. That is the fallback
    below, and it is the behaviour every import had before this.
    """
    try:
        run = sync_service.start_sync(
            db, settings, workspace_id=user.workspace_id, user_id=user.id, trigger="import"
        )
    except (sync_service.NotConnectedError, sync_service.SyncAlreadyRunningError) as exc:
        runner.submit(
            lambda: import_service.refresh_rollup_job(user.workspace_id),
            name=f"rollup-after-import-{batch_id}",
        )
        reason = (
            "not_connected"
            if isinstance(exc, sync_service.NotConnectedError)
            else "already_running"
        )
        log.info("import batch=%s did not start a sync: %s", batch_id, reason)
        return SyncAfterImportPayload(started=False, reason=reason)

    log.info("import batch=%s started sync run=%s", batch_id, run.id)
    return SyncAfterImportPayload(started=True, run_id=run.id)


class ImportNotFoundError(AppError):
    code = "import_not_found"
    status_code = 404
    message = "That import doesn't exist."
    next_step = "Go back to Import history and pick one from the list."


class SheetNotFoundError(AppError):
    code = "sheet_not_found"
    status_code = 404
    message = "That sheet isn't linked."
    next_step = "Open Settings -> Google Sheets to see the sheets you have linked."


@router.post(
    "/imports/upload",
    response_model=ImportResult,
    summary="Upload a CSV or Excel stock sheet",
)
def upload_inventory(
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    file: UploadFile = File(..., description="A .csv or .xlsx stock sheet"),
) -> ImportResult:
    """Design doc §8.2. Parses, validates and stores in one request.

    Synchronous deliberately: at the stated scale (~1,240 rows) the whole
    operation is well under a second, and a background job would add a polling
    endpoint and a job table for no benefit the user can perceive.
    """
    return _import(
        db,
        user=user,
        settings=settings,
        filename=file.filename or "upload.csv",
        raw=file.file.read(),
    )


@router.post(
    "/imports/google-sheet",
    response_model=ImportResult,
    summary="Import a Google Sheet",
)
def import_from_google_sheet(
    body: GoogleSheetImportRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
) -> ImportResult:
    """Design doc §8.4. Exports the sheet as CSV, then runs the ordinary import.

    The sheet link is translated into Google's own CSV export address and
    downloaded by the same fetcher a CSV URL uses — address guard, size cap and
    timeout included. From there it is the same import as an upload: same parser,
    same validation, same upsert, same response. Import History records the
    method as ``google_sheet``, which is the only difference.
    """
    return _sheet_import(db, user=user, settings=settings, url=body.url)


def _import(
    db: DbDep,
    *,
    user: CurrentUser,
    settings: SettingsDep,
    filename: str,
    raw: bytes,
    method: str | None = None,
) -> ImportResult:
    """Run the import and shape the response. Shared by both doorways."""
    outcome = import_service.run_file_import(
        db,
        workspace_id=user.workspace_id,
        user=user,
        filename=filename,
        raw=raw,
        max_bytes=settings.max_upload_bytes,
        method=method,
    )
    db.commit()
    db.refresh(outcome.batch)

    sync = _sync_after_import(db, settings, user, batch_id=outcome.batch.id)

    # Written once the run is known, so the import and the sync it started
    # read as one sequence. The timestamps are the real ones — the batch's
    # own start and finish — even though both rows arrive together.
    for step, at, detail in (
        ("import_started", outcome.batch.started_at, outcome.batch.origin_filename),
        (
            "inventory_imported",
            outcome.batch.finished_at,
            f"{outcome.created:,} new, {outcome.updated:,} restated, {outcome.removed:,} removed",
        ),
    ):
        activity.record(
            db,
            workspace_id=user.workspace_id,
            run_id=sync.run_id,
            batch_id=outcome.batch.id,
            step=step,
            detail=detail,
            at=at,
        )

    parse = outcome.parse
    return ImportResult(
        batch=ImportBatchSummary.model_validate(outcome.batch),
        items_created=outcome.created,
        items_updated=outcome.updated,
        items_removed=outcome.removed,
        header_row_number=parse.header_row_number,
        detected_columns=parse.detected_columns,
        rejected=[
            RejectedRowPayload(row_number=r.row_number, reason=r.reason, detail=r.detail)
            for r in parse.rejected[:MAX_REPORTED_ROWS]
        ],
        duplicates=[
            DuplicatePayload(sku=d.sku, rows=d.rows, merged_quantity=d.merged_quantity)
            for d in parse.duplicates[:MAX_REPORTED_ROWS]
        ],
        sync=sync,
        rejected_truncated=len(parse.rejected) > MAX_REPORTED_ROWS,
        duplicates_truncated=len(parse.duplicates) > MAX_REPORTED_ROWS,
        sheet_format=parse.sheet_format,
        analysis=AnalysisSummaryPayload(**outcome.analysis.__dict__),
        unmapped_reasons=parse.unmapped_reasons,
        warnings=parse.warnings,
    )


def _sheet_import(
    db: DbDep,
    *,
    user: CurrentUser,
    settings: SettingsDep,
    url: str,
    name: str | None = None,
    sheet: LinkedSheet | None = None,
) -> ImportResult:
    """Fetch a sheet, import it, and record the link. The one sheet-import path.

    Every doorway ends up here — the Import page, Settings' Link new sheet, and
    Re-sync — so all three share one fetcher, one importer and one record of
    what happened. They differ only in where the URL came from.

    A failure is written to the link before it is re-raised. ``run_file_import``
    commits its own batch when an import fails, precisely so the attempt
    survives; a linked sheet whose last attempt failed has to say so too, or
    Settings would show the previous success and hide the problem.
    """
    linked = sheet
    try:
        filename, raw = google_sheets.fetch(settings, url)
        log.info("import from google sheet: %s bytes as %s", len(raw), filename)
        result = _import(
            db,
            user=user,
            settings=settings,
            filename=filename,
            raw=raw,
            method="google_sheet",
        )
    except AppError:
        if linked is not None:
            sheets_service.record(linked, status="failed")
            db.commit()
        raise

    # Only after the import proved the sheet is readable.
    if linked is None:
        linked = sheets_service.upsert(db, workspace_id=user.workspace_id, url=url, name=name)
    elif name:
        linked.name = name

    sheets_service.record(
        linked,
        status=result.batch.status,
        batch_id=result.batch.id,
        when=result.batch.finished_at,
    )
    db.commit()
    return result


@router.get(
    "/imports/sheets",
    response_model=LinkedSheetList,
    summary="Linked Google Sheets",
)
def list_sheets(user: CurrentUser, db: DbDep) -> LinkedSheetList:
    """Design doc S13, Settings -> Google Sheets."""
    return LinkedSheetList(
        items=[
            LinkedSheetPayload.model_validate(s)
            for s in LinkedSheetRepository(db).list(user.workspace_id)
        ]
    )


@router.post(
    "/imports/sheets",
    response_model=ImportResult,
    summary="Link a Google Sheet and import it",
)
def link_sheet(
    body: LinkSheetRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
) -> ImportResult:
    """Linking imports. A link that has never been read is not worth recording."""
    return _sheet_import(db, user=user, settings=settings, url=body.url, name=body.name)


@router.post(
    "/imports/sheets/{sheet_id}/resync",
    response_model=ImportResult,
    summary="Re-import a linked sheet",
)
def resync_sheet(
    sheet_id: int,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
) -> ImportResult:
    """The same import, run again against the stored address."""
    sheet = LinkedSheetRepository(db).get(user.workspace_id, sheet_id)
    if sheet is None:
        raise SheetNotFoundError
    return _sheet_import(db, user=user, settings=settings, url=sheet.url, sheet=sheet)


@router.delete(
    "/imports/sheets/{sheet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink a Google Sheet",
)
def unlink_sheet(sheet_id: int, user: CurrentUser, db: DbDep) -> Response:
    """Forgets the address only.

    The SKUs it imported stay, and so does its Import History: unlinking is
    "stop offering to re-run this", not "undo what it brought in". Undoing an
    import is a different question, and one nothing here can answer.
    """
    repo = LinkedSheetRepository(db)
    sheet = repo.get(user.workspace_id, sheet_id)
    if sheet is None:
        raise SheetNotFoundError
    repo.delete(sheet)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/imports",
    response_model=ImportHistoryPage,
    summary="Import history",
)
def list_imports(
    user: CurrentUser,
    db: DbDep,
    status: str | None = Query(default=None, pattern="^(complete|failed)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ImportHistoryPage:
    """Design doc §8.8, newest first."""
    batches = ImportBatchRepository(db)
    return ImportHistoryPage(
        items=[
            ImportBatchSummary.model_validate(b)
            for b in batches.list(user.workspace_id, status=status, limit=limit, offset=offset)
        ],
        total=batches.count(user.workspace_id, status=status),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/imports/{batch_id}",
    response_model=ImportBatchSummary,
    summary="One import",
)
def get_import(batch_id: int, user: CurrentUser, db: DbDep) -> ImportBatchSummary:
    batch = ImportBatchRepository(db).get(user.workspace_id, batch_id)
    if batch is None:
        raise ImportNotFoundError
    return ImportBatchSummary.model_validate(batch)


@router.get(
    "/inventory/summary",
    response_model=InventorySummary,
    summary="Current stock totals",
)
def inventory_summary(user: CurrentUser, db: DbDep) -> InventorySummary:
    items = InventoryItemRepository(db)
    return InventorySummary(
        total_skus=items.count(user.workspace_id),
        total_quantity=items.total_quantity(user.workspace_id),
        last_imported_at=items.last_imported_at(user.workspace_id),
    )


@router.get(
    "/inventory",
    response_model=InventoryPage,
    summary="Current stock",
)
def list_inventory(
    user: CurrentUser,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> InventoryPage:
    items = InventoryItemRepository(db)
    return InventoryPage(
        items=[
            InventoryItemPayload.model_validate(i)
            for i in items.list(user.workspace_id, limit=limit, offset=offset)
        ],
        total=items.count(user.workspace_id),
        limit=limit,
        offset=offset,
    )
