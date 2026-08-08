"""Database snapshots.

**Why not `cp`.** The database runs in WAL mode, so at any moment the committed
state is spread across `stocksync.db` and `stocksync.db-wal`. Copying the main
file alone captures a torn database — one that opens, and is missing whatever
had not been checkpointed. SQLite's own online backup API reads through a read
transaction and produces a file that is consistent by construction, while
writers keep working.

`sqlite3.Connection.backup` is that API, in the standard library. Shelling out
to the `sqlite3` binary would do the same thing but adds a dependency the
deployment does not otherwise need and cannot be tested without it.

**Retention is by count, not age.** A workspace that goes quiet for a month
should still have its last fourteen snapshots, which an age rule would delete
precisely when nothing new was being written to replace them.

This is a command, not a schedule. Cron or a systemd timer decides when — see
``deploy/README.md`` — because a scheduler inside a single-worker API process
would run backups on the same thread that serves requests.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

#: `stocksync-20260807-114500.db`. Sorts chronologically as text, which is what
#: lets retention be a slice rather than a parse.
STAMP_FORMAT = "%Y%m%d-%H%M%S"
PREFIX = "stocksync-"
SUFFIX = ".db"


class BackupError(RuntimeError):
    """The snapshot could not be taken. Raised rather than logged: a backup
    that quietly did nothing is worse than no backup, because it is believed."""


def snapshot(settings: Settings, *, now: datetime | None = None) -> Path:
    """Write one consistent copy of the database and return its path."""
    source = settings.sqlite_path()
    if source is None:
        raise BackupError(
            "STOCKSYNC_DATABASE_URL does not point at a SQLite file. "
            "Use the server's own backup tooling for other databases."
        )
    if not source.is_file():
        raise BackupError(f"No database at {source}.")

    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime(STAMP_FORMAT)
    destination = settings.backup_dir / f"{PREFIX}{stamp}{SUFFIX}"

    # Staged then renamed, for the same reason exports are: a snapshot
    # interrupted halfway must not be left under a name that retention will
    # count as a good backup.
    staging = destination.with_suffix(destination.suffix + ".part")
    try:
        # `closing`, not a bare `with`: sqlite3's context manager commits or
        # rolls back the *transaction* and leaves the connection open. The file
        # handle then outlives the block, and the rename below fails outright on
        # Windows and leaks a descriptor everywhere else.
        with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(staging)) as copy:
            live.backup(copy)
        staging.replace(destination)
    except (sqlite3.Error, OSError) as caught:
        # Best-effort: on Windows the staging file can still be held if the
        # failure was the connection itself, and a failed cleanup must not
        # replace the error that actually matters.
        try:
            staging.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - the original error is the story
            log.warning("could not remove partial snapshot %s", staging)
        raise BackupError(f"Could not back up {source}: {caught}") from caught

    log.info("database snapshot written to %s (%s bytes)", destination, destination.stat().st_size)
    return destination


def prune(settings: Settings) -> list[Path]:
    """Delete all but the newest ``backup_keep`` snapshots. Returns what went."""
    if not settings.backup_dir.exists():
        return []

    snapshots = sorted(
        (p for p in settings.backup_dir.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()),
        reverse=True,  # newest first: the filename stamp sorts chronologically
    )
    removed: list[Path] = []
    for stale in snapshots[settings.backup_keep :]:
        try:
            stale.unlink()
            removed.append(stale)
        except OSError:  # pragma: no cover - permissions, or a vanished mount
            log.warning("could not delete old snapshot %s", stale, exc_info=True)

    # Interrupted snapshots are never a backup, whatever the retention count.
    for partial in settings.backup_dir.glob(f"{PREFIX}*{SUFFIX}.part"):
        partial.unlink(missing_ok=True)

    if removed:
        log.info("pruned %s snapshot(s), keeping %s", len(removed), settings.backup_keep)
    return removed


def run(settings: Settings, *, now: datetime | None = None) -> Path:
    """Take a snapshot, then prune. What the CLI and any scheduler call."""
    destination = snapshot(settings, now=now)
    prune(settings)
    return destination


def latest(settings: Settings) -> Path | None:
    """The newest snapshot, for a health check or a restore script."""
    if not settings.backup_dir.exists():
        return None
    snapshots = sorted(settings.backup_dir.glob(f"{PREFIX}*{SUFFIX}"), reverse=True)
    return snapshots[0] if snapshots else None
