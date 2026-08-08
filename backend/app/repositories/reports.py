"""Queries for generated reports."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Report


class ReportRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def add(self, report: Report) -> Report:
        self._db.add(report)
        self._db.flush()
        return report

    def get(self, workspace_id: int, report_id: int) -> Report | None:
        """Scoped by workspace, so an id from another workspace is a 404."""
        return self._db.scalars(
            select(Report).where(Report.id == report_id, Report.workspace_id == workspace_id)
        ).first()

    def page(
        self,
        workspace_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
        kind: str | None = None,
    ) -> tuple[list[Report], int]:
        """The Export Centre list, newest first.

        ``content`` used to be deferred here, because loading a megabyte of
        report bytes per row to print a filename made the list slower the more
        a workspace had exported. The bytes now live on disk and the row
        carries a short path, so the whole row is cheap and there is nothing
        left to defer.
        """
        stmt = select(Report).where(Report.workspace_id == workspace_id)
        if kind:
            stmt = stmt.where(Report.kind == kind)

        total = self._db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self._db.scalars(
                stmt.order_by(Report.created_at.desc(), Report.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return rows, total

    def stored_paths(self) -> set[str]:
        """Every export key any row still points at, across all workspaces.

        Deliberately unscoped: the sweep decides what to delete from disk, and
        a per-workspace answer would make every other workspace's files look
        orphaned.
        """
        return {
            path
            for path in self._db.scalars(
                select(Report.storage_path).where(Report.storage_path.is_not(None))
            )
            if path
        }

    def delete(self, report: Report) -> None:
        self._db.delete(report)

    def unfinished(self) -> list[Report]:
        """Reports still marked preparing — start-up reclaim reads these."""
        return list(self._db.scalars(select(Report).where(Report.status == "preparing")))
