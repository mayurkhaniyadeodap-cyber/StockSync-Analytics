"""The step log behind Sync History.

A sync run records what it ended up as. That is enough to know whether the
figures are current and nothing else — when a run comes back ``partial`` there
is one error string to work from and no record of how far it got.

So each step of the automatic workflow writes a row here as it happens:

    Import started → Inventory imported → Shopify sync started →
    Shopify sync completed → Analytics recompute started →
    Analytics recompute completed (or failed) → the run's final status

**Events, not state.** A row is appended and never updated, so the log is a
history rather than a status field with extra steps. The run row stays the
authority on the outcome; this says how it got there.

The import steps carry a ``batch_id`` and the sync steps a ``run_id``, and one
workflow's rows share both — which is what lets Sync History show an import and
the sync it triggered as one sequence rather than two unrelated records.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import IdMixin, UtcDateTime, utcnow

#: The steps of the automatic workflow, in the order they happen. Values are
#: stored, so they are stable identifiers rather than display text — the client
#: decides how to word them.
ACTIVITY_STEPS = (
    "import_started",
    "inventory_imported",
    "sync_started",
    "sync_completed",
    "recompute_started",
    "recompute_completed",
    "recompute_failed",
    "workflow_finished",
)

#: How a step went. ``started`` is not an outcome — it is a step that has no
#: outcome yet, which is exactly what a log of a long job needs to be able to
#: say while it is still running.
ACTIVITY_STATES = ("started", "ok", "failed")


class ActivityEvent(IdMixin, Base):
    """One step of one automatic workflow.

    No ``TimestampMixin``: an event has one time, the moment it describes, and a
    row that also carried ``updated_at`` would invite something to update it.
    """

    __tablename__ = "activity_events"
    __table_args__ = (
        # The two ways this is read: everything for one run, newest first for a
        # workspace.
        Index("ix_activity_events_run", "workspace_id", "run_id", "at"),
        Index("ix_activity_events_recent", "workspace_id", "at"),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The sync run this step belongs to, when there is one. Null for the import
    #: steps of a workflow whose sync was never started — no store connected, or
    #: a run already in flight.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=True
    )
    #: The import that began the workflow, when one did. Null for a sync started
    #: from the Shopify page or by a retry.
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=True
    )

    step: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    #: One sentence of context — how many rows, which error. Never a stack trace:
    #: this is read in the UI.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ActivityEvent {self.step!r} {self.state!r} run={self.run_id}>"
