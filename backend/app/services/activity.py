"""Recording the steps of the automatic workflow.

One function, called from the two places the workflow runs — the import route
and the sync job. Kept apart from both so that adding a step is one line at the
point it happens rather than a change to how imports or syncs are structured.

**Recording never fails the thing it is recording.** A log write that raised
would turn a working sync into a failed one over its own bookkeeping, so every
call swallows database errors and says so in the application log instead. The
sync run remains the authority on the outcome; this is the account of how it got
there.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import ActivityEvent, utcnow

log = logging.getLogger(__name__)


def record(
    db: Session,
    *,
    workspace_id: int,
    step: str,
    state: str = "ok",
    run_id: int | None = None,
    batch_id: int | None = None,
    detail: str | None = None,
    at: datetime | None = None,
    commit: bool = True,
) -> None:
    """Append one step. Never raises.

    ``at`` exists for the import steps, which are written once the run they
    started is known but describe moments that already passed — the timestamps
    are real even though the rows arrive together.

    ``commit=False`` is for callers inside a transaction they own; the sync job
    commits its own, because a step recorded and then rolled back by a later
    failure would lose exactly the account of the failure.
    """
    try:
        db.add(
            ActivityEvent(
                workspace_id=workspace_id,
                run_id=run_id,
                batch_id=batch_id,
                step=step,
                state=state,
                detail=(detail or "")[:500] or None,
                at=at or utcnow(),
            )
        )
        if commit:
            db.commit()
    except SQLAlchemyError:  # pragma: no cover - bookkeeping must not break the job
        log.warning("could not record activity step %s for run %s", step, run_id, exc_info=True)
        if commit:
            db.rollback()
