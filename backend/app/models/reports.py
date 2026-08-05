"""Generated exports — design doc §12.

One table. A row is both the request and the result: it exists the moment a
user asks for an export, carries the status they watch, and ends up holding the
bytes they download.

**The file lives in the row, not on disk.** Reports are small and bounded (a
row cap, below), and a BLOB removes a whole class of bugs a file store brings
with it — orphaned files after a failed delete, a backup that captures the
database but not the exports, path handling on a platform that treats
separators differently, and a directory the deployment has to create and keep
writable. Deleting a report becomes a DELETE, and the export is included in the
one artifact this project already tells you to back up (README, "Database").

The seam is `content`: if exports ever grow past what is comfortable inline,
this column becomes a key into object storage and nothing else has to move.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin, UtcDateTime

#: The report types of design doc §12.1, plus the dashboard snapshot the
#: Dashboard header exports — the same engine, a different builder.
REPORT_KINDS = ("inventory", "sales", "sku_performance", "dashboard", "sku_matching")

#: The three export formats of design doc §12.1.
REPORT_FORMATS = ("csv", "xlsx", "pdf")

#: §12.2's Export Centre lifecycle: Preparing → Ready, or Failed.
REPORT_STATUSES = ("preparing", "ready", "failed")

#: Bounded so one export cannot exhaust memory or bloat the database. A report
#: at this cap is already past the point of being read as a document; the row
#: count is recorded either way, so a truncated export says so rather than
#: quietly ending early.
MAX_REPORT_ROWS = 50_000

#: What "Top 50" means, and the same 50 the dashboard table shows.
TOP_ROWS_EXPORT = 50


class Report(IdMixin, TimestampMixin, Base):
    """One export, from the moment it is asked for to the moment it is deleted."""

    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_workspace_created", "workspace_id", "created_at"),)

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    fmt: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="preparing")

    #: The window the report covers, kept so the history row can describe itself
    #: without the caller having to remember what they asked for.
    range_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    range_label: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Recorded separately from ``len(content)`` so the size survives in the
    #: history after the bytes are cleared.
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Null while preparing, and null again once the report has failed.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Both halves of the error envelope (§16), stored so the Export Centre can
    # show the same sentence the user would have seen, rather than inventing one.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # Nullable so a user can be deleted without erasing the export log.
    #: Set when the user asked for the top rows only. NULL means the whole
    #: report, which is the default and what every existing row is — an export
    #: silently capped at 50 would be indistinguishable from a workspace with
    #: 50 SKUs, so the choice is recorded rather than inferred.
    row_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Report {self.id} {self.kind}/{self.fmt} {self.status}>"
