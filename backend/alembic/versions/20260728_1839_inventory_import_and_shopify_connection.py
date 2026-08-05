"""inventory import and shopify connection

M2. Three tables:

* ``import_batches``  — one row per import attempt, kept when it fails so
  Import History can say why (design doc §8.8).
* ``inventory_items`` — current stock, one row per SKU, unique on
  ``(workspace_id, sku_normalized)``. Money is an INTEGER of paise, per
  IMPLEMENTATION_PLAN.md §4.5.
* ``shopify_connections`` — one store per workspace. The Admin API token is
  stored as Fernet ciphertext, never in plaintext.

Every table carries ``workspace_id`` (plan §2.1). Index creation runs inside
``batch_alter_table`` so the migration applies on SQLite as well as on a server
dialect; alembic/env.py turns batch mode on for SQLite only.

Revision ID: 5e7a4ebec798
Revises: 4768a2b2a307
Created: 2026-07-28 18:39:27.114538
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5e7a4ebec798'
down_revision: str | None = '4768a2b2a307'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('import_batches',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('method', sa.String(length=24), nullable=False),
    sa.Column('origin_filename', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rows_read', sa.Integer(), nullable=False),
    sa.Column('rows_imported', sa.Integer(), nullable=False),
    sa.Column('rows_merged', sa.Integer(), nullable=False),
    sa.Column('rows_flagged', sa.Integer(), nullable=False),
    sa.Column('rows_rejected', sa.Integer(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('triggered_by', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], name=op.f('fk_import_batches_triggered_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_import_batches_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_batches'))
    )
    with op.batch_alter_table('import_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_batches_workspace_id'), ['workspace_id'], unique=False)
        batch_op.create_index('ix_import_batches_workspace_started', ['workspace_id', 'started_at'], unique=False)

    op.create_table('shopify_connections',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('shop_domain', sa.String(length=255), nullable=False),
    sa.Column('access_token_encrypted', sa.Text(), nullable=False),
    sa.Column('token_scopes', sa.Text(), nullable=True),
    sa.Column('store_name', sa.String(length=255), nullable=True),
    sa.Column('plan_name', sa.String(length=120), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('order_lookback_days', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disconnected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('connected_by', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['connected_by'], ['users.id'], name=op.f('fk_shopify_connections_connected_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_shopify_connections_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shopify_connections')),
    sa.UniqueConstraint('workspace_id', name='workspace_shopify_connection')
    )
    with op.batch_alter_table('shopify_connections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shopify_connections_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('inventory_items',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('sku', sa.String(length=120), nullable=False),
    sa.Column('sku_normalized', sa.String(length=120), nullable=False),
    sa.Column('product_name', sa.String(length=500), nullable=False),
    sa.Column('category', sa.String(length=120), nullable=True),
    sa.Column('price_paise', sa.Integer(), nullable=True),
    sa.Column('quantity_on_hand', sa.Integer(), nullable=False),
    sa.Column('quantity_imported', sa.Integer(), nullable=False),
    sa.Column('source_batch_id', sa.Integer(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_imported_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_batch_id'], ['import_batches.id'], name=op.f('fk_inventory_items_source_batch_id_import_batches'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_inventory_items_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_items')),
    sa.UniqueConstraint('workspace_id', 'sku_normalized', name='workspace_sku')
    )
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.create_index('ix_inventory_items_sku_normalized', ['workspace_id', 'sku_normalized'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_items_source_batch_id'), ['source_batch_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_inventory_items_workspace_id'), ['workspace_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inventory_items_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_inventory_items_source_batch_id'))
        batch_op.drop_index('ix_inventory_items_sku_normalized')

    op.drop_table('inventory_items')
    with op.batch_alter_table('shopify_connections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shopify_connections_workspace_id'))

    op.drop_table('shopify_connections')
    with op.batch_alter_table('import_batches', schema=None) as batch_op:
        batch_op.drop_index('ix_import_batches_workspace_started')
        batch_op.drop_index(batch_op.f('ix_import_batches_workspace_id'))

    op.drop_table('import_batches')
