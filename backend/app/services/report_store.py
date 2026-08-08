"""Where a generated export actually lives.

Reports used to be stored as a BLOB on their own row. That removed a class of
bugs — no orphaned files, no directory to keep writable, deleting was a DELETE —
and it worked while exports were small. It stopped being the right trade at
scale: fifty retained reports of up to 50,000 rows each sit inside the same
SQLite file that serves every analytics query, and `download` materialised one
entirely in the worker's memory before writing a byte to the socket.

So the bytes move to disk and the row keeps the metadata. What that costs, and
how each cost is paid:

* **Orphaned files.** Deleting a row deletes its file first; a file that is
  already gone is not an error. `sweep_orphans` exists for the residue —
  a crash between the two, or a row removed by something that bypassed the
  service.
* **A missing file under a `ready` row.** Treated as the report being gone
  rather than as a 500: `read` raises the same not-ready error the UI already
  renders, because from the user's side that is exactly what happened.
* **Backups.** `data/` and `storage/` are now two directories to keep, which
  `deploy/README.md` says explicitly.

**Nothing user-supplied reaches the filesystem.** The stored path is built from
the workspace id, the report id and the format — all integers and a value from
a fixed enum. `report.filename`, which *is* generated from user-visible text,
is used only in the Content-Disposition header. That is what makes traversal
structurally impossible here rather than a matter of sanitising well.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings
from app.models.reports import Report

log = logging.getLogger(__name__)


def relative_path(*, workspace_id: int, report_id: int, fmt: str) -> str:
    """The stored key: stable, unique, and free of anything a user typed.

    Relative to ``export_dir`` rather than absolute, so moving the directory —
    or restoring a backup onto a host with a different layout — does not
    invalidate every row.
    """
    return f"{workspace_id}/{report_id}.{fmt}"


def absolute_path(settings: Settings, stored: str) -> Path:
    return settings.export_dir / stored


def write(settings: Settings, *, report: Report, content: bytes) -> str:
    """Put the file on disk and return the key to record on the row.

    The row is not updated here. The caller commits the path and the status
    together, so a row can never claim `ready` in a transaction that rolled
    back the write it describes.
    """
    stored = relative_path(workspace_id=report.workspace_id, report_id=report.id, fmt=report.fmt)
    destination = absolute_path(settings, stored)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Written beside the target and moved into place, so a reader never sees a
    # half-written export: the rename is atomic within a filesystem, and a
    # crash mid-write leaves the temporary file rather than a truncated report.
    staging = destination.with_suffix(destination.suffix + ".part")
    staging.write_bytes(content)
    staging.replace(destination)
    return stored


def read(settings: Settings, report: Report) -> Path:
    """The file to send, or None if the row outlived it.

    Returns the path rather than the bytes: the whole point of the move is that
    the response streams from disk instead of through this process's memory.
    """
    if not report.storage_path:
        return Path()
    return absolute_path(settings, report.storage_path)


def remove(settings: Settings, report: Report) -> None:
    """Delete the file. Never raises — the row is the record that matters.

    A file that cannot be removed is a wasted block, and a delete that fails
    because of it would leave the user staring at a report they asked to be rid
    of. The sweep below is the second chance.
    """
    if not report.storage_path:
        return
    try:
        absolute_path(settings, report.storage_path).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - permissions, or a vanished mount
        log.warning("could not delete export %s", report.storage_path, exc_info=True)


def sweep_orphans(settings: Settings, known: set[str]) -> int:
    """Delete export files no row points at any more.

    Runs at start-up, where it costs one directory walk and catches whatever
    the crash-shaped gaps above left behind. Advisory: a failure here must not
    stop the application from serving.
    """
    root = settings.export_dir
    if not root.exists():
        return 0

    removed = 0
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            stored = path.relative_to(root).as_posix()
            # `.part` files are interrupted writes — no row will ever claim one.
            if stored in known and not stored.endswith(".part"):
                continue
            path.unlink(missing_ok=True)
            removed += 1
    except OSError:  # pragma: no cover - housekeeping, never fatal
        log.warning("could not sweep orphaned exports", exc_info=True)

    if removed:
        log.info("removed %s orphaned export file(s)", removed)
    return removed
