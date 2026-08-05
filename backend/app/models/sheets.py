"""Google Sheets a workspace imports from repeatedly.

A one-off sheet import needs nothing stored: the bytes are fetched, parsed and
upserted, and Import History records that it happened. What it cannot do is
happen *again* — the address is gone the moment the request ends, so "re-import
last week's sheet" means finding the link and pasting it a second time.

This table is that address, kept. It holds no credential and grants no access:
the sheet is public or the import fails, exactly as before. Re-syncing runs the
same fetch and the same importer against the same URL.

``name`` is supplied by the user. The sheet's real title is only readable
through the Sheets API, which needs an OAuth token this project deliberately
does not have — so the choice is a name the user typed or a fabricated one, and
a label somebody chose is worth more than a document key dressed up as a title.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin, UtcDateTime


class LinkedSheet(IdMixin, TimestampMixin, Base):
    __tablename__ = "linked_sheets"
    __table_args__ = (
        # One row per tab, not per document: two tabs of the same workbook are
        # two different sheets to import, and folding them together would make
        # linking the second silently rewrite the first.
        UniqueConstraint("workspace_id", "sheet_key", "gid", name="workspace_sheet_tab"),
        Index("ix_linked_sheets_workspace", "workspace_id"),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )

    #: What the user calls it.
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: The link as pasted, so Settings can show the user their own address back
    #: rather than the export URL this code derives from it.
    url: Mapped[str] = mapped_column(Text, nullable=False)

    #: Google's document key, and which tab. Together they identify the sheet
    #: across the different link shapes the same document can be copied as.
    sheet_key: Mapped[str] = mapped_column(String(96), nullable=False)
    #: Empty string rather than NULL: SQLite counts NULLs as distinct in a
    #: unique constraint, so a nullable column would let the same tab be linked
    #: any number of times whenever the link carried no gid.
    gid: Mapped[str] = mapped_column(String(24), nullable=False, default="")

    #: The last import run from this link — when, and how it went. Read from
    #: the batch rather than duplicated, except for these two, which Settings
    #: shows per row and which would otherwise need a join per sheet.
    last_synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: Nullable and ON DELETE SET NULL: import history can be pruned without
    #: taking the link with it.
    last_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LinkedSheet {self.name!r}>"
