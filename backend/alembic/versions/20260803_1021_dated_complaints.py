"""dated complaints

Adds ``sku_daily_complaints``: one row per (workspace, SKU, day) carrying that
day's complaint counts. It is what makes Complaint Rate % a rate over the
selected range — the counts on ``inventory_items`` are running totals with no
date, so a windowed rate had no dated numerator to divide.

Nothing is backfilled and nothing is dropped. ``inventory_items`` keeps its
complaint columns: they are still the lifetime totals, and for sheets imported
in the aggregated format they are the *only* record, since that format carries
no dates. Those complaints are reported as undated rather than assigned a date
this migration would have to invent.

Autogenerate also proposed dropping the ``server_default`` from thirteen
``inventory_items`` columns. That drift predates this change and is unrelated to
it, so it is deliberately not included: rewriting thirteen columns of a live
table to no effect is not something a migration about complaint dates should do.

Revision ID: 9da5cd93c0e9
Revises: c7f2a91b6d34
Created: 2026-08-03 10:21:35.724669
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9da5cd93c0e9"
down_revision: str | None = "c7f2a91b6d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sku_daily_complaints",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("sku_normalized", sa.String(length=120), nullable=False),
        sa.Column("complaint_date", sa.Date(), nullable=False),
        sa.Column("item_defect_partial", sa.Integer(), nullable=False),
        sa.Column("item_defect_complete", sa.Integer(), nullable=False),
        sa.Column("item_damage_partial", sa.Integer(), nullable=False),
        sa.Column("item_damage_complete", sa.Integer(), nullable=False),
        sa.Column("order_wrong_parcel", sa.Integer(), nullable=False),
        sa.Column("electronics_nonworking_partial", sa.Integer(), nullable=False),
        sa.Column("electronics_nonworking_complete", sa.Integer(), nullable=False),
        sa.Column("missing", sa.Integer(), nullable=False),
        sa.Column("missing_part", sa.Integer(), nullable=False),
        sa.Column("item_mismatch_wrong_item", sa.Integer(), nullable=False),
        sa.Column("total_complaints", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_sku_daily_complaints_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "sku_normalized",
            "complaint_date",
            name=op.f("pk_sku_daily_complaints"),
        ),
    )
    with op.batch_alter_table("sku_daily_complaints", schema=None) as batch_op:
        batch_op.create_index(
            "ix_sku_daily_complaints_cover",
            ["workspace_id", "complaint_date", "sku_normalized", "total_complaints"],
            unique=False,
        )
        batch_op.create_index(
            "ix_sku_daily_complaints_date",
            ["workspace_id", "complaint_date"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("sku_daily_complaints", schema=None) as batch_op:
        batch_op.drop_index("ix_sku_daily_complaints_date")
        batch_op.drop_index("ix_sku_daily_complaints_cover")

    op.drop_table("sku_daily_complaints")
