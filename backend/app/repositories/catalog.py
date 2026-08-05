"""Queries for synced Shopify orders and their line items.

Every method here is written to be called once per *batch*, never once per row.
SQLite serialises writes, so a 100k-line order sync issued as 100k statements
would hold the write lock long enough for a user saving a setting to time out
(plan §4.5). The upserts take lists and the lookups take lists of ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, OrderLineItem

# Kept under SQLite's default SQLITE_MAX_VARIABLE_NUMBER (999 on older builds)
# with room for several bind parameters per row.
CHUNK = 400


def _chunks(values: list[Any], size: int = CHUNK) -> list[list[Any]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


class OrderRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def orders_by_shopify_id(self, workspace_id: int, shopify_ids: list[int]) -> dict[int, Order]:
        found: dict[int, Order] = {}
        for window in _chunks(shopify_ids):
            rows = self._db.scalars(
                select(Order).where(
                    Order.workspace_id == workspace_id,
                    Order.shopify_order_id.in_(window),
                )
            )
            for row in rows:
                found[row.shopify_order_id] = row
        return found

    def add_order(self, order: Order) -> Order:
        self._db.add(order)
        return order

    def count(self, workspace_id: int) -> int:
        """Orders already downloaded for this workspace.

        Read by a recompute-only retry, which reports the orders it is
        recomputing over rather than the zero it fetched — the rollup stage
        takes a count of nothing to mean there is nothing to do.
        """
        return int(
            self._db.scalar(select(func.count(Order.id)).where(Order.workspace_id == workspace_id))
            or 0
        )

    def line_items_by_shopify_id(
        self, workspace_id: int, shopify_ids: list[int]
    ) -> dict[int, OrderLineItem]:
        found: dict[int, OrderLineItem] = {}
        for window in _chunks(shopify_ids):
            rows = self._db.scalars(
                select(OrderLineItem).where(
                    OrderLineItem.workspace_id == workspace_id,
                    OrderLineItem.shopify_line_item_id.in_(window),
                )
            )
            for row in rows:
                found[row.shopify_line_item_id] = row
        return found

    def add_line_item(self, item: OrderLineItem) -> OrderLineItem:
        self._db.add(item)
        return item

    def count_orders(self, workspace_id: int) -> int:
        return (
            self._db.scalar(select(func.count(Order.id)).where(Order.workspace_id == workspace_id))
            or 0
        )

    def latest_created_at(self, workspace_id: int) -> datetime | None:
        """When the newest synced order was *created* in Shopify.

        ``created_at_shopify``, not ``processed_at``: it is the field Shopify's
        ``created_at_min`` filter compares against, so anchoring an incremental
        sync on anything else would ask for the wrong window.
        """
        return self._db.scalar(
            select(func.max(Order.created_at_shopify)).where(Order.workspace_id == workspace_id)
        )

    def latest_processed_at(self, workspace_id: int) -> datetime | None:
        """When the newest synced order was placed.

        ``processed_at``, not ``synced_at``: the question is how far through the
        store's timeline we have got, not when we last wrote a row.
        """
        return self._db.scalar(
            select(func.max(Order.processed_at)).where(Order.workspace_id == workspace_id)
        )

    def count_line_items(self, workspace_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count(OrderLineItem.id)).where(
                    OrderLineItem.workspace_id == workspace_id
                )
            )
            or 0
        )

    def count_skus_with_sales(self, workspace_id: int) -> int:
        """Distinct SKUs that actually sold — the set a sheet SKU can match."""
        return (
            self._db.scalar(
                select(func.count(func.distinct(OrderLineItem.sku_normalized))).where(
                    OrderLineItem.workspace_id == workspace_id,
                    OrderLineItem.sku_normalized != "",
                )
            )
            or 0
        )
