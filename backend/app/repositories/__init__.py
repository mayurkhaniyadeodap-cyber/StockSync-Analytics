"""Data access.

Every query lives here rather than in a service or a route. The point is not
ceremony — it is that the services below stay readable as *rules* ("a duplicate
SKU merges by summing quantity") without a `select()` in the middle of the
sentence, and that swapping SQLite for PostgreSQL later touches this layer and
nothing above it.

Repositories take an open ``Session`` and never commit. Transaction boundaries
belong to the route handler, so one request is one transaction.
"""

from app.repositories.catalog import OrderRepository
from app.repositories.inventory import ImportBatchRepository, InventoryItemRepository
from app.repositories.sheets import LinkedSheetRepository
from app.repositories.shopify import ShopifyConnectionRepository, SyncRunRepository

__all__ = [
    "ImportBatchRepository",
    "InventoryItemRepository",
    "LinkedSheetRepository",
    "OrderRepository",
    "ShopifyConnectionRepository",
    "SyncRunRepository",
]
