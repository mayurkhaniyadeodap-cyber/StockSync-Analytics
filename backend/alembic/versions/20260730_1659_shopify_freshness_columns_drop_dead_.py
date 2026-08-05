"""Shopify freshness columns; drop the dead product columns from sync_runs

Two changes, both consequences of the catalogue removal:

* ``shopify_connections`` gains ``store_latest_order_at`` and
  ``freshness_checked_at``. Staleness used to be measured by comparing the
  rollup against the orders table, which says whether the derived layer is
  current and nothing about whether the orders themselves are. Recording where
  the *store* had got to is what makes "you are 34 hours behind" sayable.

* ``sync_runs`` loses ``products_pct``, ``products_synced``, ``variants_synced``
  and ``cursor_products``. Nothing has written them since the catalogue was
  dropped, so every row read 0 products synced and the UI drew a progress bar
  that could only sit at 0%. All existing values are 0, so no information is
  lost by removing them.

Autogenerate also proposed stripping ``server_default`` from the thirteen count
columns on ``inventory_items``. That is deliberately **not** included: the
defaults are what let those NOT NULL columns be added to a populated table in
33d0bd196f2a, removing them would rewrite the whole table for no behavioural
change, and they are what keeps a future ALTER on that table possible.

Revision ID: 9deabe96aeb8
Revises: 33d0bd196f2a
Created: 2026-07-30 16:59:39.927002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9deabe96aeb8"
down_revision: str | None = "33d0bd196f2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shopify_connections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("store_latest_order_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("freshness_checked_at", sa.DateTime(timezone=True), nullable=True)
        )

    with op.batch_alter_table("sync_runs", schema=None) as batch_op:
        batch_op.drop_column("products_pct")
        batch_op.drop_column("products_synced")
        batch_op.drop_column("variants_synced")
        batch_op.drop_column("cursor_products")


def downgrade() -> None:
    with op.batch_alter_table("sync_runs", schema=None) as batch_op:
        # server_default is required, not optional: these are NOT NULL and the
        # table has rows, so SQLite cannot add them without a value to backfill.
        batch_op.add_column(
            sa.Column("products_pct", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("products_synced", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("variants_synced", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("cursor_products", sa.Text(), nullable=True))

    with op.batch_alter_table("shopify_connections", schema=None) as batch_op:
        batch_op.drop_column("freshness_checked_at")
        batch_op.drop_column("store_latest_order_at")
