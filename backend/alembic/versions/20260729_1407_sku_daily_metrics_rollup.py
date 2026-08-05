"""sku daily metrics rollup

M5, plan §4.1 Layer 1. One row per (workspace, SKU, day), derived from
``order_line_items`` and recomputed rather than incremented during ingest.

Measured justification: aggregating the raw line items for a single dashboard
widget takes ~684 ms at 429,000 rows, and the store produces roughly 2.3
million rows per 90-day window — about 3.7 s per query, with seven widgets on
the page. Rolled up, the same window is tens of thousands of rows.

The grain is the primary key: no surrogate id, because
``(workspace_id, sku_normalized, metric_date)`` *is* the identity, and it gives
the date-range scans the dashboard needs for free. Revenue is INTEGER paise
(plan §4.5); cancelled and refunded orders are excluded when the rollup is
built, once, so no downstream query has to remember to.

Revision ID: 8cb6a247b36e
Revises: 82960bc531b1
Created: 2026-07-29 14:07:11.619997
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '8cb6a247b36e'
down_revision: str | None = '82960bc531b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sku_daily_metrics',
    sa.Column('workspace_id', sa.Integer(), nullable=False),
    sa.Column('sku_normalized', sa.String(length=120), nullable=False),
    sa.Column('metric_date', sa.Date(), nullable=False),
    sa.Column('units_sold', sa.Integer(), nullable=False),
    sa.Column('revenue_paise', sa.Integer(), nullable=False),
    sa.Column('order_count', sa.Integer(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_sku_daily_metrics_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'sku_normalized', 'metric_date', name=op.f('pk_sku_daily_metrics'))
    )
    with op.batch_alter_table('sku_daily_metrics', schema=None) as batch_op:
        batch_op.create_index('ix_sku_daily_metrics_date', ['workspace_id', 'metric_date'], unique=False)
        batch_op.create_index(
            "ix_sku_daily_metrics_cover",
            [
                "workspace_id",
                "metric_date",
                "sku_normalized",
                "units_sold",
                "revenue_paise",
                "order_count",
            ],
            unique=False,
        )



def downgrade() -> None:
    with op.batch_alter_table('sku_daily_metrics', schema=None) as batch_op:
        batch_op.drop_index("ix_sku_daily_metrics_cover")
        batch_op.drop_index('ix_sku_daily_metrics_date')

    op.drop_table('sku_daily_metrics')
