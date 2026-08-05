"""Queries for generated reports."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

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

        ``content`` is deferred: the history renders names and sizes, and
        loading a megabyte of report bytes per row to print a filename would
        make the list slower the more the workspace has exported.
        """
        stmt = select(Report).where(Report.workspace_id == workspace_id)
        if kind:
            stmt = stmt.where(Report.kind == kind)

        total = self._db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self._db.scalars(
                stmt.options(defer(Report.content))
                .order_by(Report.created_at.desc(), Report.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return rows, total

    def delete(self, report: Report) -> None:
        self._db.delete(report)

    def unfinished(self) -> list[Report]:
        """Reports still marked preparing — start-up reclaim reads these."""
        return list(self._db.scalars(select(Report).where(Report.status == "preparing")))
