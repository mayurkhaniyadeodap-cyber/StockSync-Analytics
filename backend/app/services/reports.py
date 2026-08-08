"""Generating, listing and deleting exports — design doc §12.

**Generation runs on the background worker**, like a sync or a match run. Not
because a 125-row CSV is slow — it is milliseconds — but because §12.2's flow
is *Preparing → Ready → Download*, and a status a user watches has to be a real
one. Wiring the same states to a synchronous call would mean the UI showed
"Preparing" it had invented. At the 50,000-row cap a PDF is genuinely slow
enough to matter, so the state is honest at both ends of the range.

**A report is a snapshot, not a live query.** Once ready, the bytes never
change: it is a record of what the numbers were when it was taken, which is the
only thing that makes "the export I emailed you last Tuesday" a meaningful
sentence. Re-running the same report makes a second row.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.db.session import get_session_factory
from app.models import (
    MAX_REPORT_ROWS,
    REPORT_FORMATS,
    REPORT_KINDS,
    TOP_ROWS_EXPORT,
    Report,
    utcnow,
)
from app.repositories.reports import ReportRepository
from app.services import report_data, report_files, report_store
from app.workers import runner

log = logging.getLogger(__name__)

#: How many reports one workspace keeps. The Export Centre is a convenience,
#: not an archive: without a cap the table grows without bound and every row
#: holds its bytes. The oldest are dropped as new ones are made.
HISTORY_LIMIT = 50

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class UnknownReportError(AppError):
    code = "unknown_report"
    status_code = 400
    message = "That report type isn't one StockSync Analytics can build."
    next_step = "Choose Inventory, Sales or SKU performance."


class ReportNotFoundError(AppError):
    code = "report_not_found"
    status_code = 404
    message = "That report no longer exists."
    next_step = "Generate it again from the Reports page."


class ReportNotReadyError(AppError):
    code = "report_not_ready"
    status_code = 409
    message = "That report isn't ready to download yet."
    next_step = "Wait for it to finish preparing, then try again."


def filename_for(
    kind: str, fmt: str, when: datetime | None = None, report_id: int | None = None
) -> str:
    """A name that sorts chronologically and survives any filesystem.

    The report's own id is appended when there is one. A second-resolution
    timestamp alone collided for two exports of the same type and format made in
    the same second — different rows, identical filenames, and a browser that
    overwrites one download with the other. The id is unique by construction, so
    no clock resolution can defeat it.
    """
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{report_id}" if report_id is not None else ""
    return _UNSAFE.sub("-", f"stocksync-{kind.replace('_', '-')}-{stamp}{suffix}.{fmt}")


def preview(
    db: Session, *, workspace_id: int, kind: str, days: int, limit: int = 12
) -> report_data.ReportTable:
    """The first rows of a report, without generating a file (§12.1).

    The preview and the export call the same builder, so what the user checks
    before clicking Export is what they get.
    """
    _validate(kind, "csv")
    # The limit goes into the query. Building the whole report and slicing meant
    # a preview of 12 rows cost a full 13,006-row export on the real store.
    return report_data.build(db, kind=kind, workspace_id=workspace_id, days=days, limit=limit)


def request_report(
    db: Session,
    settings: Settings,
    *,
    workspace_id: int,
    user_id: int | None,
    kind: str,
    fmt: str,
    range_option: str,
    top_only: bool = False,
) -> Report:
    """Record the request, queue the work, return the row immediately."""
    _validate(kind, fmt)
    days, label = report_data.range_days_for(range_option)

    reports = ReportRepository(db)
    report = reports.add(
        Report(
            workspace_id=workspace_id,
            kind=kind,
            fmt=fmt,
            status="preparing",
            range_days=days,
            range_label=label,
            row_limit=TOP_ROWS_EXPORT if top_only else None,
            filename=filename_for(kind, fmt),
            requested_by=user_id,
        )
    )
    # `add` flushes, so the id exists now; naming after it is what makes the
    # filename unique rather than merely usually-unique.
    report.filename = filename_for(kind, fmt, report_id=report.id)
    db.commit()

    report_id = report.id
    runner.submit(
        lambda: run_report_job(settings, report_id=report_id, workspace_id=workspace_id),
        name=f"report-{report_id}",
    )
    log.info("report queued id=%s kind=%s fmt=%s workspace=%s", report_id, kind, fmt, workspace_id)
    return report


def run_report_job(settings: Settings, *, report_id: int, workspace_id: int) -> None:
    """Build the file and store it. Owns its own session — it runs on a thread."""
    factory = get_session_factory()
    with factory() as db:
        report = ReportRepository(db).get(workspace_id, report_id)
        if report is None:  # deleted while it sat in the queue
            return
        try:
            table = report_data.build(
                db,
                kind=report.kind,
                workspace_id=workspace_id,
                days=report.range_days or 30,
                # NULL means everything. `truncated` still counts the true total,
                # so a top-only export says on its face that it is one.
                limit=report.row_limit or MAX_REPORT_ROWS,
            )
            content = report_files.render(table, report.fmt)
            # Written before the row is marked ready and committed with it, so
            # a `ready` row always describes a file that reached disk. The
            # reverse order would let a crash between the two leave a report
            # the Export Centre offers and the download cannot find.
            report.storage_path = report_store.write(settings, report=report, content=content)
            report.size_bytes = len(content)
            report.row_count = len(table.rows)
            report.status = "ready"
            report.error_code = None
            report.error_detail = None
        except Exception as caught:  # a worker thread must not die silently
            log.exception("report %s failed", report_id)
            report.status = "failed"
            # Whatever landed before the failure is not a report anyone can
            # use, and leaving it would be an orphan the sweep has to find.
            report_store.remove(settings, report)
            report.storage_path = None
            report.error_code = "report_failed"
            # The user's half of the error envelope (§16). The exception text is
            # in the log; what reaches the screen says what to do instead.
            report.error_detail = f"{type(caught).__name__} while building the file."
        report.completed_at = utcnow()
        db.commit()
        _prune(db, settings, workspace_id)


def _prune(db: Session, settings: Settings, workspace_id: int) -> None:
    """Drop the oldest rows past the cap. Advisory — never fails the job."""
    try:
        rows, total = ReportRepository(db).page(workspace_id, limit=HISTORY_LIMIT + 100, offset=0)
        for stale in rows[HISTORY_LIMIT:]:
            # File first: a row removed without its file is an orphan nothing
            # points at, and only the start-up sweep would ever find it.
            report_store.remove(settings, stale)
            db.delete(stale)
        if total > HISTORY_LIMIT:
            db.commit()
    except SQLAlchemyError:  # pragma: no cover - housekeeping, never fatal
        log.warning("could not prune report history", exc_info=True)
        db.rollback()


def reclaim_interrupted(db: Session) -> int:
    """Fail reports left preparing by a stopped process.

    Same reasoning as the sync and match reclaims: the worker is a thread, so
    nothing that was preparing when the process exited still is. Left alone the
    row would spin forever in the Export Centre with no way to clear it.
    """
    stale = ReportRepository(db).unfinished()
    for report in stale:
        report.status = "failed"
        report.error_code = "report_interrupted"
        report.error_detail = "The server restarted while this report was being prepared."
        report.completed_at = utcnow()
    if stale:
        db.commit()
        log.warning("reclaimed %s interrupted report(s)", len(stale))
    return len(stale)


def download(
    db: Session, settings: Settings, *, workspace_id: int, report_id: int
) -> tuple[Report, Path]:
    """The row and the file to stream from.

    Returns a path rather than bytes: streaming from disk is the whole reason
    the file left the database, and reading it here to hand back would put it
    straight back into this process's memory.

    A `ready` row whose file has gone raises the not-ready error rather than a
    500. From the user's side those are the same event — the export is not
    there — and the Export Centre already renders that state.
    """
    report = ReportRepository(db).get(workspace_id, report_id)
    if report is None:
        raise ReportNotFoundError
    if report.status != "ready" or not report.storage_path:
        raise ReportNotReadyError

    path = report_store.read(settings, report)
    if not path.is_file():
        log.warning("report %s is ready but its file is missing: %s", report_id, path)
        raise ReportNotReadyError
    return report, path


def delete(db: Session, settings: Settings, *, workspace_id: int, report_id: int) -> None:
    repository = ReportRepository(db)
    report = repository.get(workspace_id, report_id)
    if report is None:
        raise ReportNotFoundError
    # File first, for the same reason as the prune above: a delete that removed
    # the row and failed on the file would leave bytes nothing can reach.
    report_store.remove(settings, report)
    repository.delete(report)
    db.commit()
    log.info("report deleted id=%s workspace=%s", report_id, workspace_id)


def _validate(kind: str, fmt: str) -> None:
    if kind not in REPORT_KINDS or fmt not in REPORT_FORMATS:
        raise UnknownReportError
