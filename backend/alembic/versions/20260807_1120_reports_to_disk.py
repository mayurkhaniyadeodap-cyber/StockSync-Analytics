"""Move report bytes out of the database and onto disk

The `content` BLOB was the right call while exports were small: no orphaned
files, no directory to keep writable, and deleting a report was a DELETE. At the
50,000-row cap with 50 reports retained per workspace it stopped being right —
those bytes live in the same SQLite file that answers every analytics query, and
downloading one read it whole into the worker's memory.

**Existing exports are moved, not discarded.** Reports are snapshots people
refer back to ("the export I sent you last Tuesday"), so this walks the ready
rows, writes each blob to `STOCKSYNC_EXPORT_DIR` under the same key the
application will look for, and only then drops the column. A row whose write
fails is marked failed with a reason rather than left pointing at nothing.

The downgrade is honest about being lossy: it can restore the column and read
the files back, but any export written after the upgrade whose file has since
been swept has no bytes to return.

Revision ID: 5a83e6c40f17
Revises: 3f1c7a9d5e21
Create Date: 2026-08-07 11:20:00.000000
"""

from __future__ import annotations

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.config import get_settings

revision = "5a83e6c40f17"
down_revision = "3f1c7a9d5e21"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def _export_root() -> Path:
    return get_settings().export_dir


def upgrade() -> None:
    op.add_column("reports", sa.Column("storage_path", sa.String(length=255), nullable=True))

    connection = op.get_bind()
    root = _export_root()
    rows = connection.execute(
        sa.text(
            "SELECT id, workspace_id, fmt, content FROM reports "
            "WHERE content IS NOT NULL AND status = 'ready'"
        )
    ).fetchall()

    moved = 0
    for report_id, workspace_id, fmt, content in rows:
        stored = f"{workspace_id}/{report_id}.{fmt}"
        destination = root / stored
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        except OSError:
            # A report nobody can download must not claim to be ready. The row
            # keeps its metadata so the Export Centre can still explain itself.
            log.warning("could not write export %s; marking it failed", stored, exc_info=True)
            connection.execute(
                sa.text(
                    "UPDATE reports SET status = 'failed', error_code = 'report_unmigrated', "
                    "error_detail = :detail WHERE id = :id"
                ),
                {
                    "id": report_id,
                    "detail": "This export could not be moved to disk storage. Generate it again.",
                },
            )
            continue

        connection.execute(
            sa.text("UPDATE reports SET storage_path = :path WHERE id = :id"),
            {"id": report_id, "path": stored},
        )
        moved += 1

    if rows:
        log.info("moved %s of %s stored export(s) to %s", moved, len(rows), root)

    # Batch mode is configured globally in env.py for SQLite, which cannot drop
    # a column in place — it rebuilds the table.
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("content")


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.add_column(sa.Column("content", sa.LargeBinary(), nullable=True))

    connection = op.get_bind()
    root = _export_root()
    rows = connection.execute(
        sa.text("SELECT id, storage_path FROM reports WHERE storage_path IS NOT NULL")
    ).fetchall()

    for report_id, stored in rows:
        source = root / stored
        if not source.is_file():
            # Nothing to put back. Left as-is rather than invented: `download`
            # on the old code treats a NULL content as not-ready, which is the
            # truthful answer.
            continue
        connection.execute(
            sa.text("UPDATE reports SET content = :content WHERE id = :id"),
            {"id": report_id, "content": source.read_bytes()},
        )

    with op.batch_alter_table("reports") as batch:
        batch.drop_column("storage_path")
