"""One live sync run per workspace, enforced by a partial unique index.

`start_sync` checked `runs.active()` and inserted if it found nothing, with
nothing atomic between the two. Ten concurrent callers let two runs through in
testing, and both then fetched the same Shopify window and rebuilt the rollup.

A partial unique index settles it in the only place a check-then-insert can be
made atomic. Both SQLite and PostgreSQL support the form.

Revision ID: 3f1c7a9d5e21
Revises: 11ea97959b52
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "3f1c7a9d5e21"
down_revision: str | None = "11ea97959b52"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # A database written before this index could already hold more than one live
    # run per workspace — that is the defect being closed. Creating a unique
    # index over existing duplicates fails, so they are reconciled first.
    #
    # The newest row is kept live and the older ones are closed as interrupted,
    # which is the truthful description: they were started, they are not
    # running, and nothing finished them. Their cursors are left in place so a
    # retry resumes rather than restarting.
    op.execute(
        sa.text(
            """
            UPDATE sync_runs
               SET status = 'finished',
                   result = COALESCE(result, 'partial'),
                   error_code = COALESCE(error_code, 'sync_interrupted'),
                   finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)
             WHERE status IN ('queued', 'running')
               AND id NOT IN (
                     SELECT MAX(id) FROM sync_runs
                      WHERE status IN ('queued', 'running')
                      GROUP BY workspace_id
                   )
            """
        )
    )
    op.create_index(
        "uq_sync_runs_one_live_per_workspace",
        "sync_runs",
        ["workspace_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_sync_runs_one_live_per_workspace", table_name="sync_runs")
