"""activity events

Adds ``activity_events``: one row per step of the automatic workflow, so Sync
History can show how a run reached its result rather than only what the result
was. Append-only — the sync run stays the authority on the outcome.

Autogenerate also proposed dropping the ``server_default`` from thirteen
``inventory_items`` columns. That drift predates this change and is unrelated to
it, so it is deliberately not included — for the same reason the dated-complaints
migration excluded it: rewriting thirteen columns of a live table to no effect is
not something a migration about an event log should do.

Revision ID: 11ea97959b52
Revises: 9da5cd93c0e9
Created: 2026-08-04 16:22:46.729818
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "11ea97959b52"
down_revision: str | None = "9da5cd93c0e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_events",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name=op.f("fk_activity_events_batch_id_import_batches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["sync_runs.id"],
            name=op.f("fk_activity_events_run_id_sync_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_activity_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_events")),
    )
    with op.batch_alter_table("activity_events", schema=None) as batch_op:
        batch_op.create_index("ix_activity_events_recent", ["workspace_id", "at"], unique=False)
        batch_op.create_index(
            "ix_activity_events_run", ["workspace_id", "run_id", "at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_events_workspace_id"), ["workspace_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("activity_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_activity_events_workspace_id"))
        batch_op.drop_index("ix_activity_events_run")
        batch_op.drop_index("ix_activity_events_recent")

    op.drop_table("activity_events")
