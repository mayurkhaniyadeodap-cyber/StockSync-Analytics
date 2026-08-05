"""Record when an export was deliberately limited to the top rows

Additive and nullable. NULL means "the whole report", which is what every
existing row is and what the default stays — so nothing about an export made
before this changes meaning.

Revision ID: c7f2a91b6d34
Revises: b41c7f0d2e18
Create Date: 2026-07-31 18:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7f2a91b6d34"
down_revision = "b41c7f0d2e18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("row_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "row_limit")
