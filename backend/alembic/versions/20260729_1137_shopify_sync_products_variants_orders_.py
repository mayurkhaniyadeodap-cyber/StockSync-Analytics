"""shopify sync: products, variants, orders and sync runs

M3. Five tables, all upserted on Shopify's own ids rather than on our
surrogate keys, so a re-sync updates in place instead of accumulating copies:

* ``sync_runs``        — one row per pull. ``cursor_products``/``cursor_orders``
  are what make a partial sync resumable rather than merely labelled.
* ``shopify_products`` / ``shopify_variants`` — the catalogue. The SKU lives on
  the variant, and its ``sku_normalized`` index is deliberately NOT unique:
  two variants sharing a SKU is the condition M4's Duplicates queue surfaces.
* ``orders`` / ``order_line_items`` — sales. ``sku_at_sale`` preserves the SKU
  as it was when the sale happened, so a sale still reconciles after the
  variant is renamed or deleted.

Shopify ids are BigInteger: they exceed 2^31 and would truncate silently on a
32-bit INTEGER in a server dialect. Money is INTEGER paise (plan §4.5). Every
table carries ``workspace_id`` (plan §2.1). Indexes are created inside
``batch_alter_table`` so this applies on SQLite as well as on a server dialect.

Revision ID: 0b168b9245d9
Revises: 5e7a4ebec798
Created: 2026-07-29 11:37:29.876063
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0b168b9245d9'
down_revision: str | None = '5e7a4ebec798'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('orders',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('connection_id', sa.Integer(), nullable=False),
    sa.Column('shopify_order_id', sa.BigInteger(), nullable=False),
    sa.Column('order_number', sa.String(length=64), nullable=True),
    sa.Column('created_at_shopify', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('financial_status', sa.String(length=32), nullable=True),
    sa.Column('fulfillment_status', sa.String(length=32), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('total_price_paise', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['connection_id'], ['shopify_connections.id'], name=op.f('fk_orders_connection_id_shopify_connections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_orders_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('workspace_id', 'shopify_order_id', name='workspace_order')
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_connection_id'), ['connection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_workspace_id'), ['workspace_id'], unique=False)
        batch_op.create_index('ix_orders_workspace_processed', ['workspace_id', 'processed_at'], unique=False)

    op.create_table('shopify_products',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('connection_id', sa.Integer(), nullable=False),
    sa.Column('shopify_product_id', sa.BigInteger(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('handle', sa.String(length=255), nullable=True),
    sa.Column('product_type', sa.String(length=255), nullable=True),
    sa.Column('vendor', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['connection_id'], ['shopify_connections.id'], name=op.f('fk_shopify_products_connection_id_shopify_connections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_shopify_products_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shopify_products')),
    sa.UniqueConstraint('workspace_id', 'shopify_product_id', name='workspace_product')
    )
    with op.batch_alter_table('shopify_products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shopify_products_connection_id'), ['connection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_shopify_products_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('sync_runs',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('connection_id', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('products_pct', sa.Integer(), nullable=False),
    sa.Column('orders_pct', sa.Integer(), nullable=False),
    sa.Column('products_synced', sa.Integer(), nullable=False),
    sa.Column('variants_synced', sa.Integer(), nullable=False),
    sa.Column('orders_synced', sa.Integer(), nullable=False),
    sa.Column('line_items_synced', sa.Integer(), nullable=False),
    sa.Column('result', sa.String(length=16), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('retry_after_seconds', sa.Integer(), nullable=True),
    sa.Column('cursor_products', sa.Text(), nullable=True),
    sa.Column('cursor_orders', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('triggered_by', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['connection_id'], ['shopify_connections.id'], name=op.f('fk_sync_runs_connection_id_shopify_connections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], name=op.f('fk_sync_runs_triggered_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_sync_runs_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_runs'))
    )
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sync_runs_connection_id'), ['connection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sync_runs_workspace_id'), ['workspace_id'], unique=False)
        batch_op.create_index('ix_sync_runs_workspace_started', ['workspace_id', 'started_at'], unique=False)

    op.create_table('order_line_items',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('shopify_line_item_id', sa.BigInteger(), nullable=False),
    sa.Column('shopify_variant_id', sa.BigInteger(), nullable=True),
    sa.Column('sku_at_sale', sa.String(length=120), nullable=True),
    sa.Column('sku_normalized', sa.String(length=120), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price_paise', sa.Integer(), nullable=True),
    sa.Column('total_discount_paise', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_line_items_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_order_line_items_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_line_items')),
    sa.UniqueConstraint('workspace_id', 'shopify_line_item_id', name='workspace_line_item')
    )
    with op.batch_alter_table('order_line_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_order_line_items_order_id'), ['order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_order_line_items_shopify_variant_id'), ['shopify_variant_id'], unique=False)
        batch_op.create_index('ix_order_line_items_sku', ['workspace_id', 'sku_normalized'], unique=False)
        batch_op.create_index(batch_op.f('ix_order_line_items_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('shopify_variants',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('shopify_variant_id', sa.BigInteger(), nullable=False),
    sa.Column('sku', sa.String(length=120), nullable=True),
    sa.Column('sku_normalized', sa.String(length=120), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('price_paise', sa.Integer(), nullable=True),
    sa.Column('inventory_quantity', sa.Integer(), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['shopify_products.id'], name=op.f('fk_shopify_variants_product_id_shopify_products'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_shopify_variants_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shopify_variants')),
    sa.UniqueConstraint('workspace_id', 'shopify_variant_id', name='workspace_variant')
    )
    with op.batch_alter_table('shopify_variants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shopify_variants_product_id'), ['product_id'], unique=False)
        batch_op.create_index('ix_shopify_variants_sku_normalized', ['workspace_id', 'sku_normalized'], unique=False)
        batch_op.create_index(batch_op.f('ix_shopify_variants_workspace_id'), ['workspace_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('shopify_variants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shopify_variants_workspace_id'))
        batch_op.drop_index('ix_shopify_variants_sku_normalized')
        batch_op.drop_index(batch_op.f('ix_shopify_variants_product_id'))

    op.drop_table('shopify_variants')
    with op.batch_alter_table('order_line_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_order_line_items_workspace_id'))
        batch_op.drop_index('ix_order_line_items_sku')
        batch_op.drop_index(batch_op.f('ix_order_line_items_shopify_variant_id'))
        batch_op.drop_index(batch_op.f('ix_order_line_items_order_id'))

    op.drop_table('order_line_items')
    with op.batch_alter_table('sync_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_sync_runs_workspace_started')
        batch_op.drop_index(batch_op.f('ix_sync_runs_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_sync_runs_connection_id'))

    op.drop_table('sync_runs')
    with op.batch_alter_table('shopify_products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shopify_products_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_shopify_products_connection_id'))

    op.drop_table('shopify_products')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index('ix_orders_workspace_processed')
        batch_op.drop_index(batch_op.f('ix_orders_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_orders_connection_id'))

    op.drop_table('orders')
