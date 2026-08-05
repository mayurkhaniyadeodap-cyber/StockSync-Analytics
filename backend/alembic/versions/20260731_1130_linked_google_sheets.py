"""Linked Google Sheets

One table, additive. Nothing existing changes, so this migration is safe to run
against a populated database and reversible without data loss beyond the links
themselves — the imports those links produced live in ``import_batches`` and are
untouched by the downgrade.

Revision ID: b41c7f0d2e18
Revises: 9deabe96aeb8
Create Date: 2026-07-31 11:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b41c7f0d2e18"
down_revision = "9deabe96aeb8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linked_sheets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sheet_key", sa.String(length=96), nullable=False),
        # Empty string rather than NULL, so the unique constraint below actually
        # constrains: SQLite treats NULLs as distinct from one another. The
        # default is Python-side, as everywhere else in this schema — a
        # server_default here would show up as a permanent autogenerate diff
        # against the model, and the table is new so nothing needs backfilling.
        sa.Column("gid", sa.String(length=24), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_batch_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: pruning import history must not silently
        # unlink the sheets that history came from.
        sa.ForeignKeyConstraint(["last_batch_id"], ["import_batches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "sheet_key", "gid", name="workspace_sheet_tab"),
    )
    op.create_index("ix_linked_sheets_workspace", "linked_sheets", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_linked_sheets_workspace", table_name="linked_sheets")
    op.drop_table("linked_sheets")
