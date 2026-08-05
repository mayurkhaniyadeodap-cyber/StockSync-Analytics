"""Engine, session factory, and the database health probe.

This module is the only place that knows which database is in use. Everything
above it works against SQLAlchemy's abstractions, so changing
``STOCKSYNC_DATABASE_URL`` to a server dialect needs no changes elsewhere.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def _engine_kwargs(settings: Settings) -> dict[str, Any]:
    """Connection arguments appropriate to the configured dialect."""
    if settings.is_sqlite:
        return {
            # FastAPI runs sync endpoints on a threadpool, so a pooled
            # connection is used from more than one thread over its lifetime.
            # SQLite's own guard against that is too strict for this pattern;
            # SQLAlchemy's pool already serialises access to each connection.
            "connect_args": {
                "check_same_thread": False,
                "timeout": settings.db_busy_timeout_seconds,
            },
        }

    # Client/server dialects: pool sizing and liveness checks apply.
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }


def _register_sqlite_pragmas(engine: Engine, settings: Settings) -> None:
    """Apply the pragmas SQLite needs to behave like a relational database.

    None of these are optional for this application:

    * ``foreign_keys`` is OFF by default in SQLite. Without it, every foreign
      key in the schema is documentation rather than a constraint, and an
      orphaned sku_link or order_line_item would go unnoticed.
    * ``journal_mode=WAL`` lets readers proceed during a write. A Shopify sync
      writing order lines would otherwise block the dashboard for its duration.
    * ``busy_timeout`` makes a competing writer wait instead of failing
      immediately with "database is locked".
    * ``synchronous=NORMAL`` is the documented safe pairing with WAL: durable
      against process crashes, and far faster than FULL on bulk inserts.
    """
    timeout_ms = int(settings.db_busy_timeout_seconds * 1000)

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # PRAGMA does not accept bound parameters; timeout_ms is an int we
            # computed, never user input.
            cursor.execute(f"PRAGMA busy_timeout={timeout_ms}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def ensure_database_parent(settings: Settings) -> None:
    """Create the SQLite file's directory if it does not exist.

    A file-backed SQLite database cannot create its own parent, and the failure
    is an opaque "unable to open database file". Alembic builds its own engine,
    so this lives outside get_engine() and both paths call it.
    """
    sqlite_path = settings.sqlite_path()
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    ensure_database_parent(settings)

    engine = create_engine(settings.database_url, future=True, **_engine_kwargs(settings))

    if settings.is_sqlite:
        _register_sqlite_pragmas(engine, settings)

    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that always closes."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@dataclass(frozen=True)
class DatabaseStatus:
    ok: bool
    latency_ms: float | None = None
    reason: str | None = None


def ping_database() -> DatabaseStatus:
    """Round-trip ``SELECT 1``.

    Never raises — the health endpoint needs to *report* a database failure, not
    become one.
    """
    started = time.perf_counter()
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Full exception (which can contain the connection string) goes to the
        # redacting logger; the caller gets the exception class only.
        log.warning("database ping failed: %s", exc)
        return DatabaseStatus(ok=False, reason=type(exc).__name__)
    except Exception as exc:  # a probe must report failure, never propagate it
        log.warning("database ping failed: %s", exc)
        return DatabaseStatus(ok=False, reason=type(exc).__name__)

    elapsed_ms = (time.perf_counter() - started) * 1000
    return DatabaseStatus(ok=True, latency_ms=round(elapsed_ms, 2))
