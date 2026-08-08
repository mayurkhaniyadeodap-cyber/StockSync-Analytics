"""Report endpoints — design doc §12."""

from __future__ import annotations

import logging
import urllib.parse

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbDep, SettingsDep, enforce_rate_limit
from app.repositories.reports import ReportRepository
from app.schemas.reports import (
    KIND_PATTERN,
    RANGE_PATTERN,
    ReportColumn,
    ReportCreate,
    ReportPage,
    ReportPayload,
    ReportPreview,
)
from app.services import report_data, report_files
from app.services import reports as reports_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["reports"], prefix="/reports")


@router.get("/preview", response_model=ReportPreview, summary="What the export will contain")
def preview(
    user: CurrentUser,
    db: DbDep,
    kind: str = Query(pattern=KIND_PATTERN),
    range_option: str = Query(default="30", pattern=RANGE_PATTERN),
    limit: int = Query(default=12, ge=1, le=100),
) -> ReportPreview:
    """§12.2's Preview step. Same builder as the export, so they cannot differ."""
    days, _label = report_data.range_days_for(range_option)
    table = reports_service.preview(
        db, workspace_id=user.workspace_id, kind=kind, days=days, limit=limit
    )
    return ReportPreview(
        title=table.title,
        subtitle=table.subtitle,
        columns=[ReportColumn(header=c.header, align=c.align) for c in table.columns],
        rows=[list(row) for row in table.rows],
        truncated=table.truncated,
    )


@router.post(
    "",
    response_model=ReportPayload,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate a report",
)
def create(
    user: CurrentUser, db: DbDep, settings: SettingsDep, body: ReportCreate
) -> ReportPayload:
    """202: the row comes back "preparing" and the file is built on the worker."""
    # A 50,000-row PDF is the slowest thing this application renders, and it
    # renders on the same single worker thread as every sync and import.
    enforce_rate_limit(settings, user, operation="report", what="report")
    report = reports_service.request_report(
        db,
        settings,
        workspace_id=user.workspace_id,
        user_id=user.id,
        kind=body.kind,
        fmt=body.fmt,
        range_option=body.range_option,
        top_only=body.top_only,
    )
    # The job may already have finished when the runner is inline (tests), so
    # the row is re-read rather than returning the pre-job state.
    db.expire_all()
    fresh = ReportRepository(db).get(user.workspace_id, report.id)
    return ReportPayload.model_validate(fresh or report, from_attributes=True)


@router.get("", response_model=ReportPage, summary="The Export Centre")
def history(
    user: CurrentUser,
    db: DbDep,
    kind: str | None = Query(default=None, pattern=KIND_PATTERN),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReportPage:
    db.expire_all()
    rows, total = ReportRepository(db).page(
        user.workspace_id, limit=limit, offset=offset, kind=kind
    )
    return ReportPage(
        items=[ReportPayload.model_validate(row, from_attributes=True) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{report_id}", response_model=ReportPayload, summary="One report's status")
def detail(user: CurrentUser, db: DbDep, report_id: int) -> ReportPayload:
    """Polled while a report is preparing (§12.2)."""
    db.expire_all()
    report = ReportRepository(db).get(user.workspace_id, report_id)
    if report is None:
        raise reports_service.ReportNotFoundError
    return ReportPayload.model_validate(report, from_attributes=True)


@router.get("/{report_id}/download", summary="Download the file")
def download(user: CurrentUser, db: DbDep, settings: SettingsDep, report_id: int) -> Response:
    report, path = reports_service.download(
        db, settings, workspace_id=user.workspace_id, report_id=report_id
    )
    # RFC 6266: the plain filename for old clients, the percent-encoded UTF-8
    # one for everything else. The name is generated, but quoting it means a
    # future user-supplied name can never break out of the header.
    quoted = urllib.parse.quote(report.filename)
    # FileResponse streams the file in chunks and sets Content-Length from the
    # file itself. The previous Response(content=...) read the whole export into
    # this worker's memory first, which at the 50,000-row cap is megabytes held
    # for the length of a download the client may be doing over a slow link.
    return FileResponse(
        path,
        media_type=report_files.CONTENT_TYPES.get(report.fmt, "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{report.filename}\"; filename*=UTF-8''{quoted}"
            ),
            # A report is a snapshot and its id is unique, so it can be cached
            # hard — but only by the one browser that authenticated for it.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a report")
def remove(user: CurrentUser, db: DbDep, settings: SettingsDep, report_id: int) -> Response:
    reports_service.delete(db, settings, workspace_id=user.workspace_id, report_id=report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
