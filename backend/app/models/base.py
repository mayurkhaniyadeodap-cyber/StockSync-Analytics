"""Column conventions shared by every model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect, Integer, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    Defaults are applied in Python rather than as server defaults so the value is
    identical on SQLite and PostgreSQL. SQLite has no native timestamp type and
    would otherwise store whatever CURRENT_TIMESTAMP renders.
    """
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """A datetime column that is always timezone-aware UTC in Python.

    SQLite stores datetimes as text and hands them back **naive**, even when the
    column is declared ``DateTime(timezone=True)``. Comparing one of those to an
    aware ``datetime.now(UTC)`` raises TypeError, and the failure surfaces far
    from its cause — typically as a 500 inside a session-expiry check.

    Normalising in one type rather than at each call site means no model or
    service ever has to think about it, and behaviour is identical on
    PostgreSQL, where the value comes back aware already.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # Naive input is treated as UTC rather than rejected: a caller that
            # forgot tzinfo almost certainly meant UTC, and guessing local time
            # would silently shift stored timestamps.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        # The DBAPI hands back Any; narrow it before touching tzinfo so a
        # surprising driver type fails loudly here rather than downstream.
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class IdMixin:
    """Surrogate integer primary key.

    Integers over UUIDs: this is an internal single-workspace tool where the row
    count is not sensitive, and narrow keys keep SQLite's indexes small.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin:
    """created_at / updated_at, maintained by the ORM."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
