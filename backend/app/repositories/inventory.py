"""Queries for import batches and inventory items."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from app.models import ImportBatch, InventoryItem


class ImportBatchRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, batch: ImportBatch) -> ImportBatch:
        self._db.add(batch)
        # Flushed, not committed: the caller needs the generated id to stamp
        # inventory rows, but the transaction is the route's to close.
        self._db.flush()
        return batch

    def get(self, workspace_id: int, batch_id: int) -> ImportBatch | None:
        return self._db.scalars(
            select(ImportBatch).where(
                ImportBatch.id == batch_id,
                ImportBatch.workspace_id == workspace_id,
            )
        ).first()

    def _scoped(self, workspace_id: int, status: str | None) -> Select[tuple[ImportBatch]]:
        stmt = select(ImportBatch).where(ImportBatch.workspace_id == workspace_id)
        if status == "complete":
            # The history filter's "Complete" tab means "landed", which includes
            # a partial import — rows did arrive. Only 'failed' is the failure.
            stmt = stmt.where(ImportBatch.status.in_(("complete", "partial")))
        elif status == "failed":
            stmt = stmt.where(ImportBatch.status == "failed")
        return stmt

    def list(
        self,
        workspace_id: int,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ImportBatch]:
        stmt = (
            self._scoped(workspace_id, status)
            # id as a tiebreak: two imports in the same millisecond would
            # otherwise come back in an arbitrary order and paginate wrongly.
            .order_by(ImportBatch.started_at.desc(), ImportBatch.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._db.scalars(stmt))

    def count(self, workspace_id: int, *, status: str | None = None) -> int:
        stmt = self._scoped(workspace_id, status).with_only_columns(func.count(ImportBatch.id))
        return self._db.scalar(stmt) or 0


class InventoryItemRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, item: InventoryItem) -> InventoryItem:
        self._db.add(item)
        return item

    def normalized_skus(self, workspace_id: int) -> set[str]:
        """Every SKU the workspace currently holds, as its matching key.

        Read before an import replaces the dataset, so the result can say how
        many SKUs were new, restated and removed — a count that cannot be
        recovered afterwards.
        """
        return set(
            self._db.scalars(
                select(InventoryItem.sku_normalized).where(
                    InventoryItem.workspace_id == workspace_id
                )
            )
        )

    def clear(self, workspace_id: int) -> None:
        """Delete every inventory row for the workspace.

        An import states the whole dataset, so the previous one is removed
        rather than merged with. Only ever called inside the import's own
        transaction, which the route commits after every replacement row is
        written — a failure anywhere between the two rolls the delete back with
        it, so there is no window where the workspace holds nothing.

        How many went is not returned: the caller compares the SKU sets before
        and after, which distinguishes removed from restated. A row count cannot.
        """
        self._db.execute(delete(InventoryItem).where(InventoryItem.workspace_id == workspace_id))

    def count(self, workspace_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count(InventoryItem.id)).where(
                    InventoryItem.workspace_id == workspace_id
                )
            )
            or 0
        )

    def total_quantity(self, workspace_id: int) -> int:
        """The imported quantity, summed. ``total_qty``, like every other screen.

        The Import page shows this beside the dashboard's own Total quantity
        card; summing a different column here made the two disagree by 28,280
        units on rows written before the importer reconciled them.
        """
        return (
            self._db.scalar(
                select(func.coalesce(func.sum(InventoryItem.total_qty), 0)).where(
                    InventoryItem.workspace_id == workspace_id
                )
            )
            or 0
        )

    def last_imported_at(self, workspace_id: int) -> datetime | None:
        return self._db.scalar(
            select(func.max(InventoryItem.last_imported_at)).where(
                InventoryItem.workspace_id == workspace_id
            )
        )

    def list(self, workspace_id: int, *, limit: int = 50, offset: int = 0) -> list[InventoryItem]:
        return list(
            self._db.scalars(
                select(InventoryItem)
                .where(InventoryItem.workspace_id == workspace_id)
                .order_by(InventoryItem.sku_normalized)
                .limit(limit)
                .offset(offset)
            )
        )
