"""Queries for linked Google Sheets."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LinkedSheet


class LinkedSheetRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list(self, workspace_id: int) -> list[LinkedSheet]:
        """Every linked sheet, newest first — Settings shows them all."""
        return list(
            self._db.scalars(
                select(LinkedSheet)
                .where(LinkedSheet.workspace_id == workspace_id)
                .order_by(LinkedSheet.created_at.desc(), LinkedSheet.id.desc())
            )
        )

    def get(self, workspace_id: int, sheet_id: int) -> LinkedSheet | None:
        """Scoped by workspace, so an id from another workspace is a 404."""
        return self._db.scalars(
            select(LinkedSheet).where(
                LinkedSheet.id == sheet_id, LinkedSheet.workspace_id == workspace_id
            )
        ).first()

    def find(self, workspace_id: int, *, sheet_key: str, gid: str) -> LinkedSheet | None:
        """The row for this tab, whichever link shape it was pasted as."""
        return self._db.scalars(
            select(LinkedSheet).where(
                LinkedSheet.workspace_id == workspace_id,
                LinkedSheet.sheet_key == sheet_key,
                LinkedSheet.gid == gid,
            )
        ).first()

    def add(self, sheet: LinkedSheet) -> LinkedSheet:
        self._db.add(sheet)
        self._db.flush()
        return sheet

    def delete(self, sheet: LinkedSheet) -> None:
        self._db.delete(sheet)
