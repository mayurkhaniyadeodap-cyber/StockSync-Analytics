"""Turning a stored UTC timestamp into the workspace's own calendar day.

Every timestamp in this database is UTC, which is right for storage and wrong
for reporting. The workspace runs in ``Asia/Kolkata``, so a sale at 04:00 UTC
happened at 09:30 that morning to the people who made it — and one at 22:00 UTC
happened at 03:30 the *next* day. Bucketing on the UTC day therefore files a
share of every day's sales under the wrong one: measured on the live database,
**73,979 of 418,131 orders (17.7%) fall between 00:00 and 05:30 UTC**, which is
the previous day in India.

The workspace has carried a ``timezone`` column since M1 and nothing read it.
This is the module that reads it.

**One expression, two dialects.** SQLite spells the shift as a modifier on
``date()`` and PostgreSQL as an interval, and neither understands the other. A
custom construct with a per-dialect compiler is SQLAlchemy's sanctioned answer,
and it keeps the calling code free of any dialect knowledge — the rule that lets
this database be replaced.

**A fixed offset, and the limit that implies.** The offset is resolved from the
IANA zone once per query rather than per row. For ``Asia/Kolkata`` that is
exact: India has had no daylight saving since 1945. For a zone that does observe
it, rows on the far side of a transition are attributed to the day the current
offset implies rather than the one their own offset would. Correcting that needs
per-row conversion, which neither SQLite nor a portable expression can do, and
would cost the covering index. Stated here rather than discovered later.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement, FunctionElement

log = logging.getLogger(__name__)

#: Anything that compiles to a column: a mapped attribute, a Core column, an
#: expression. Mapped attributes are `InstrumentedAttribute`, which is not a
#: `ColumnElement` to the type checker even though it behaves as one here.
ColumnExpression = ColumnElement[Any] | Any

#: Used when the stored zone name is one the platform does not know. UTC keeps
#: the previous behaviour rather than failing a dashboard over a typo in a
#: settings field.
FALLBACK_OFFSET_MINUTES = 0


def offset_minutes(timezone_name: str | None, *, at: datetime | None = None) -> int:
    """The zone's offset from UTC, in minutes, as of ``at`` (default now).

    Resolved through ``zoneinfo`` so the answer tracks the platform's tz
    database rather than a table this project would have to maintain.

    **A failure here is logged, not swallowed.** Falling back to UTC is the right
    behaviour — a mistyped zone must not take the dashboard down — but it is also
    exactly what happens when the tz database is missing entirely, which is the
    default on Windows. Silent in both cases, every workspace would quietly
    report in UTC and the timezone support would appear to work while doing
    nothing. ``tzdata`` is a dependency for this reason; the warning is what says
    so if it ever goes missing.
    """
    if not timezone_name:
        return FALLBACK_OFFSET_MINUTES
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning(
            "time zone %r could not be resolved — reporting this workspace in UTC. "
            "If this is a valid IANA name, the tz database is missing: check that "
            "the 'tzdata' package is installed.",
            timezone_name,
        )
        return FALLBACK_OFFSET_MINUTES
    moment = (at or datetime.now(UTC)).astimezone(zone)
    delta = moment.utcoffset()
    return int(delta.total_seconds() // 60) if delta is not None else FALLBACK_OFFSET_MINUTES


class local_day(FunctionElement[str]):
    """``DATE(column shifted by N minutes)`` — the calendar day in the workspace's zone.

    Usage::

        day = local_day(Order.processed_at, offset_minutes("Asia/Kolkata"))

    With an offset of zero it compiles to a plain ``DATE(column)``, so a
    UTC workspace produces byte-identical SQL to the version before this module
    existed.
    """

    type = Date()  # type: ignore[assignment]  # narrower than the base's TypeEngine[str]
    inherit_cache = True

    def __init__(self, column: ColumnExpression, minutes: int) -> None:
        super().__init__(column)
        self.minutes = int(minutes)


@compiles(local_day, "sqlite")
def _local_day_sqlite(element: local_day, compiler: object, **kw: object) -> str:
    column = compiler.process(next(iter(element.clauses)), **kw)  # type: ignore[attr-defined]
    if element.minutes == 0:
        return f"DATE({column})"
    # SQLite's date() takes modifiers; the sign has to be explicit.
    return f"DATE({column}, '{element.minutes:+d} minutes')"


@compiles(local_day, "postgresql")
def _local_day_postgresql(element: local_day, compiler: object, **kw: object) -> str:
    column = compiler.process(next(iter(element.clauses)), **kw)  # type: ignore[attr-defined]
    if element.minutes == 0:
        return f"DATE({column})"
    return f"DATE({column} + INTERVAL '{element.minutes} minutes')"


@compiles(local_day)
def _local_day_default(element: local_day, compiler: object, **kw: object) -> str:
    """Any other dialect: no shift, and the previous UTC behaviour.

    Silently wrong beats a crash on a dialect nobody has configured, and the two
    dialects this project supports are both handled above.
    """
    column = compiler.process(next(iter(element.clauses)), **kw)  # type: ignore[attr-defined]
    return f"DATE({column})"
