"""The trailing date window, as pure arithmetic.

Lives in ``core`` for the reason ``core.calc`` does: two modules need it and
neither may import the other. ``services.analytics`` reads windows and
``services.workspace_time`` resolves the offset one is computed with, so a
window function living in either would make the pair circular.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def window_for(days: int, *, offset_minutes: int = 0) -> tuple[date, date]:
    """Inclusive ``[since, until]`` for a trailing window ending *today*.

    "Today" is the day it is in the workspace's own zone, which is what
    ``offset_minutes`` shifts to. At an offset of zero this is the UTC window the
    application computed before workspace time existed, so a UTC workspace sees
    no change at all.

    Inclusive at both ends: ``days=30`` is thirty days including today, not
    thirty days before it.
    """
    until = (datetime.now(UTC) + timedelta(minutes=offset_minutes)).date()
    return until - timedelta(days=days - 1), until
