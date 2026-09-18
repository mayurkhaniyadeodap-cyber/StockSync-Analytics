"""Index the two columns every dashboard read takes a max() of

Every Analytics response reports when its figures were last rebuilt and whether
they have fallen behind the last sync. Both answers are a `max()` over a large
table, and neither column was indexed:

* ``max(sku_daily_metrics.computed_at)`` — 721,906 rows, 214 ms, and it runs
  **twice** per request: once for `last_computed_at` on the payload, and again
  inside the staleness check.
* ``max(orders.synced_at)`` — 624,636 rows, 265 ms.

That is roughly 700 ms of the 965 ms `/analytics/overview` took, and the same
again for `/analytics/insights`, on metadata rather than on any figure the user
reads. Measured on the production database after this change, both drop to 0 ms:
SQLite answers `max()` on an indexed column by seeking the last key, and with
the workspace leading the index it never leaves it.

These are pure read optimisations. No column, constraint, default or value
changes, so nothing that reads or writes these tables behaves differently.

Building them takes a few seconds on a database this size and holds no write
lock that matters — the tables are appended to by the sync, which retries on a
busy database.

Revision ID: 7c91d2b4f8a3
Revises: 5a83e6c40f17
Create Date: 2026-09-18 09:40:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "7c91d2b4f8a3"
down_revision = "5a83e6c40f17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_sku_daily_metrics_computed",
        "sku_daily_metrics",
        ["workspace_id", "computed_at"],
    )
    op.create_index(
        "ix_orders_workspace_synced",
        "orders",
        ["workspace_id", "synced_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_workspace_synced", table_name="orders")
    op.drop_index("ix_sku_daily_metrics_computed", table_name="sku_daily_metrics")
