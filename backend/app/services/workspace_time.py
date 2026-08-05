"""The workspace's calendar, resolved once per query.

Two callers need the same answer and must not compute it differently: the rollup
decides which day a sale belongs to, and the read layer decides which days are
inside "the last 30 days". If those disagree the window includes days the rollup
never filled, and the boundary of every range reports short.

So both come through here.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.window import window_for
from app.models import Workspace
from app.models.localtime import offset_minutes


def workspace_timezone(db: Session, workspace_id: int) -> str:
    """The IANA zone this workspace reports in. Defaults to UTC if unset."""
    return db.scalar(select(Workspace.timezone).where(Workspace.id == workspace_id)) or "UTC"


def workspace_offset_minutes(db: Session, workspace_id: int) -> int:
    """The workspace's current offset from UTC, in minutes."""
    return offset_minutes(workspace_timezone(db, workspace_id))


def workspace_window(db: Session, workspace_id: int, days: int) -> tuple[date, date]:
    """Inclusive [since, until] for a trailing window ending on the workspace's today."""
    return window_for(days, offset_minutes=workspace_offset_minutes(db, workspace_id))
