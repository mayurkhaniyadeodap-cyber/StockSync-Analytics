"""sku matching: links, runs and candidates

M4. Three tables:

* ``sku_links``        — the durable decision. Keyed on
  ``(workspace_id, sku_normalized)`` and holding Shopify's own variant id,
  because an inventory row is replaced by every re-import and a variant row by
  every re-sync — a foreign key to either would break the "remembered
  permanently" promise (design doc §10.2) on the very next import.
  ``link_type='suppressed_missing'`` records "Mark as missing" so a dismissed
  SKU stays dismissed.
* ``match_runs``       — one pass, with the four queue counts the tabs render.
* ``match_candidates`` — each SKU's placement (rank 0) plus its ranked drawer
  alternatives (rank 1+). Replaced wholesale per run: they describe that run's
  view, and a stale candidate is worse than none.

Every table carries ``workspace_id`` (plan §2.1); indexes are created inside
``batch_alter_table`` so this applies on SQLite and on a server dialect alike.

Revision ID: 82960bc531b1
Revises: 0b168b9245d9
Created: 2026-07-29 12:53:22.426976
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '82960bc531b1'
down_revision: str | None = '0b168b9245d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('match_runs',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('matched_count', sa.Integer(), nullable=False),
    sa.Column('review_count', sa.Integer(), nullable=False),
    sa.Column('missing_count', sa.Integer(), nullable=False),
    sa.Column('duplicate_count', sa.Integer(), nullable=False),
    sa.Column('auto_linked_count', sa.Integer(), nullable=False),
    sa.Column('skus_considered', sa.Integer(), nullable=False),
    sa.Column('progress_pct', sa.Integer(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('triggered_by', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], name=op.f('fk_match_runs_triggered_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_match_runs_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_runs'))
    )
    with op.batch_alter_table('match_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_runs_workspace_id'), ['workspace_id'], unique=False)
        batch_op.create_index('ix_match_runs_workspace_started', ['workspace_id', 'started_at'], unique=False)

    op.create_table('match_candidates',
    sa.Column('match_run_id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('sku_normalized', sa.String(length=120), nullable=False),
    sa.Column('sku', sa.String(length=120), nullable=False),
    sa.Column('product_name', sa.String(length=500), nullable=True),
    sa.Column('quantity_on_hand', sa.Integer(), nullable=True),
    sa.Column('queue', sa.String(length=16), nullable=False),
    sa.Column('shopify_variant_id', sa.BigInteger(), nullable=True),
    sa.Column('variant_sku', sa.String(length=120), nullable=True),
    sa.Column('variant_title', sa.String(length=500), nullable=True),
    sa.Column('product_title', sa.String(length=500), nullable=True),
    sa.Column('confidence', sa.Integer(), nullable=True),
    sa.Column('rule', sa.String(length=16), nullable=True),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['match_run_id'], ['match_runs.id'], name=op.f('fk_match_candidates_match_run_id_match_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_match_candidates_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_candidates'))
    )
    with op.batch_alter_table('match_candidates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_match_candidates_match_run_id'), ['match_run_id'], unique=False)
        batch_op.create_index('ix_match_candidates_run_queue', ['match_run_id', 'queue'], unique=False)
        batch_op.create_index('ix_match_candidates_sku', ['workspace_id', 'sku_normalized'], unique=False)
        batch_op.create_index(batch_op.f('ix_match_candidates_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('sku_links',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('sku_normalized', sa.String(length=120), nullable=False),
    sa.Column('shopify_variant_id', sa.BigInteger(), nullable=True),
    sa.Column('variant_row_id', sa.Integer(), nullable=True),
    sa.Column('link_type', sa.String(length=24), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('stale_reason', sa.String(length=64), nullable=True),
    sa.Column('confidence_at_confirm', sa.Integer(), nullable=True),
    sa.Column('confirmed_by', sa.Integer(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_sku_links_confirmed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['variant_row_id'], ['shopify_variants.id'], name=op.f('fk_sku_links_variant_row_id_shopify_variants'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_sku_links_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sku_links')),
    sa.UniqueConstraint('workspace_id', 'sku_normalized', name='workspace_sku_link')
    )
    with op.batch_alter_table('sku_links', schema=None) as batch_op:
        batch_op.create_index('ix_sku_links_variant', ['workspace_id', 'shopify_variant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sku_links_workspace_id'), ['workspace_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('sku_links', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sku_links_workspace_id'))
        batch_op.drop_index('ix_sku_links_variant')

    op.drop_table('sku_links')
    with op.batch_alter_table('match_candidates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_match_candidates_workspace_id'))
        batch_op.drop_index('ix_match_candidates_sku')
        batch_op.drop_index('ix_match_candidates_run_queue')
        batch_op.drop_index(batch_op.f('ix_match_candidates_match_run_id'))

    op.drop_table('match_candidates')
    with op.batch_alter_table('match_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_match_runs_workspace_started')
        batch_op.drop_index(batch_op.f('ix_match_runs_workspace_id'))

    op.drop_table('match_runs')
