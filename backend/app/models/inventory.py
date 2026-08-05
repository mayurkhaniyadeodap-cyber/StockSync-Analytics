"""Inventory import: the batch record and the current stock it produces.

Plan §2.2. Two tables in M2 — a log of every import attempt, and the current
state of stock keyed by normalised SKU. ``import_rows`` (per-row audit) and
``inventory_snapshots`` (stock history) belong to later milestones and are not
needed by anything M2 renders.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin, UtcDateTime

# Import lifecycle. The three terminal values match the prototype's three badges
# (§8.8); the in-flight ones exist so a failure can say which stage it died in.
IMPORT_STATUSES = ("pending", "reading", "validating", "saving", "complete", "partial", "failed")

# How the sheet arrived. The two `_url`/`_sheet` values are fetches rather than
# uploads — the same import either way, but History has to be able to say which,
# because a fetched source can be re-run from the same address and an upload
# cannot. The Excel URL method (§8.3) will add its own value in the same way.
IMPORT_METHODS = ("csv_upload", "excel_upload", "google_sheet")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: The ten complaint categories, as (attribute, sheet header). One list, used by
#: the importer to map headers, by the analytics to sum "Total complaints", and
#: by the table to name its columns — so the three can never disagree about what
#: a complaint is.
COMPLAINT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("item_defect_partial", "Item Defect Partial"),
    ("item_defect_complete", "Item Defect Complete"),
    ("item_damage_partial", "Item Damage Partial"),
    ("item_damage_complete", "Item Damage Complete"),
    ("order_wrong_parcel", "Order Wrong Parcel"),
    ("electronics_nonworking_partial", "Electronics Item Nonworking Partial"),
    ("electronics_nonworking_complete", "Electronics Item Nonworking Complete"),
    ("missing", "Missing"),
    ("missing_part", "Missing Part"),
    ("item_mismatch_wrong_item", "Item Mismatch Wrong Item Delivered"),
)

#: The sheet's own count columns, same idea.
COUNT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("total_count", "Total Count"),
    ("total_orders", "Total Orders"),
    ("total_qty", "Total Qty"),
)


def normalize_sku(sku: str) -> str:
    """Fold a SKU to its comparison form.

    Plan §3.1. Case and separators are the two things that differ between the
    same SKU written in a spreadsheet and in Shopify ("DD-1001", "dd 1001",
    "dd_1001"), so both are removed. Stored alongside the original, never
    instead of it — the sheet's spelling is what the user recognises.
    """
    return _NON_ALNUM.sub("", sku.strip().lower())


class ImportBatch(IdMixin, TimestampMixin, Base):
    """One import attempt, successful or not.

    Kept even when it fails: "why did last night's import not land" is a
    question the Import History screen exists to answer (§8.8).
    """

    __tablename__ = "import_batches"
    __table_args__ = (Index("ix_import_batches_workspace_started", "workspace_id", "started_at"),)

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    method: Mapped[str] = mapped_column(String(24), nullable=False)
    origin_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    # Counts drive the summary screen (§8.7) and the history table (§8.8).
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_flagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Both halves of the error envelope (§16), stored so History can show the
    # same sentence the user saw when it failed rather than inventing one.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # Nullable so a user can be deleted without erasing the import log.
    triggered_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    items: Mapped[list[InventoryItem]] = relationship(back_populates="source_batch")

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ImportBatch {self.id} {self.status!r} {self.origin_filename!r}>"


class InventoryItem(IdMixin, TimestampMixin, Base):
    """Current stock for one SKU. Replaced in place by each import.

    Unique on ``(workspace_id, sku_normalized)`` — this is *current state*, not
    a ledger. Plan §2.2 and Q1.
    """

    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "sku_normalized", name="workspace_sku"),
        Index("ix_inventory_items_sku_normalized", "workspace_id", "sku_normalized"),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # As written in the sheet, for display. Lookup uses the normalised form.
    sku: Mapped[str] = mapped_column(String(120), nullable=False)
    sku_normalized: Mapped[str] = mapped_column(String(120), nullable=False)

    product_name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Paise, not a decimal. Plan §4.5: SQLite has no exact decimal type, and
    # float money is wrong everywhere. Nullable — price is an optional column.
    price_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Q1 is still open: with no sales data yet these are written to the same
    # value by every import. They are modelled separately now because the answer
    # to Q1 makes them diverge, and adding a column later is a migration.
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The rest of the sheet. The uploaded file is the source of truth for every
    # one of these; Shopify contributes only units sold, joined on the SKU.
    #
    # Explicit columns rather than a JSON blob: the format is fixed, the
    # dashboard sums named fields, and a typed integer column is what lets
    # "Total complaints" be one SQL expression instead of ten JSON extracts.
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The ten complaint categories, in the sheet's own order. Their sum is the
    # "Total complaints" card; see COMPLAINT_COLUMNS below, which is the single
    # place that list is written down.
    item_defect_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_defect_complete: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_damage_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_damage_complete: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_wrong_parcel: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    electronics_nonworking_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    electronics_nonworking_complete: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_part: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_mismatch_wrong_item: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_imported_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    source_batch: Mapped[ImportBatch | None] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InventoryItem {self.sku!r} qty={self.quantity_on_hand}>"
