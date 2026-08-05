"""Queries for the Shopify connection and its sync log."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import ShopifyConnection, SyncRun


class ShopifyConnectionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, workspace_id: int) -> ShopifyConnection | None:
        """The workspace's connection row, whatever its status.

        Returns disconnected rows too. Callers that need a usable credential
        check ``is_live``; hiding the row here would make reconnecting look
        like a first-time connection and lose the original connected_at.
        """
        return self._db.scalars(
            select(ShopifyConnection).where(ShopifyConnection.workspace_id == workspace_id)
        ).first()

    def add(self, connection: ShopifyConnection) -> ShopifyConnection:
        self._db.add(connection)
        self._db.flush()
        return connection


class SyncRunRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, run: SyncRun) -> SyncRun:
        self._db.add(run)
        self._db.flush()  # assigns the id the caller polls on
        return run

    def get(self, workspace_id: int, run_id: int) -> SyncRun | None:
        return self._db.scalars(
            select(SyncRun).where(SyncRun.id == run_id, SyncRun.workspace_id == workspace_id)
        ).first()

    def active(self, workspace_id: int) -> SyncRun | None:
        """The run currently queued or in flight, if any.

        One at a time is enforced on this: starting a second sync while the
        first is mid-flight would have both writing the same upserts, and
        SQLite would serialise them into a long lock rather than run them
        faster.
        """
        return self._db.scalars(
            select(SyncRun)
            .where(SyncRun.workspace_id == workspace_id, SyncRun.status.in_(("queued", "running")))
            .order_by(SyncRun.id.desc())
        ).first()

    def latest(self, workspace_id: int) -> SyncRun | None:
        return self._db.scalars(
            select(SyncRun)
            .where(SyncRun.workspace_id == workspace_id)
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        ).first()

    def latest_success(self, workspace_id: int) -> SyncRun | None:
        """The most recent run that completed its window cleanly.

        An incremental sync anchors on the orders already held, which is only
        safe when the window behind them was actually finished — otherwise a gap
        left by a partial run would be skipped rather than backfilled.
        """
        return self._db.scalars(
            select(SyncRun)
            .where(SyncRun.workspace_id == workspace_id, SyncRun.result == "success")
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        ).first()

    def last_success_at(self, workspace_id: int) -> datetime | None:
        """Design doc §1.4: the "Synced N minutes ago" label on every screen."""
        return self._db.scalar(
            select(func.max(SyncRun.finished_at)).where(
                SyncRun.workspace_id == workspace_id,
                SyncRun.result.in_(("success", "partial")),
            )
        )

    def _scoped(self, workspace_id: int, result: str | None) -> Select[tuple[SyncRun]]:
        stmt = select(SyncRun).where(SyncRun.workspace_id == workspace_id)
        if result:
            stmt = stmt.where(SyncRun.result == result)
        return stmt

    def list(
        self,
        workspace_id: int,
        *,
        result: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SyncRun]:
        stmt = (
            self._scoped(workspace_id, result)
            # id as a tiebreak: two runs in the same millisecond would
            # otherwise paginate unstably.
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._db.scalars(stmt))

    def count(self, workspace_id: int, *, result: str | None = None) -> int:
        return (
            self._db.scalar(
                self._scoped(workspace_id, result).with_only_columns(func.count(SyncRun.id))
            )
            or 0
        )
