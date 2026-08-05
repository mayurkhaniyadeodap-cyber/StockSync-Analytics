"""Keeping track of the Google Sheets a workspace imports from.

Bookkeeping only. Nothing here fetches, parses, validates or upserts — a linked
sheet import is the same ``google_sheets.fetch`` followed by the same
``imports.run_file_import`` a one-off import runs, and the only thing this
module adds is remembering the address so it can be run again.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import LinkedSheet, utcnow
from app.repositories import LinkedSheetRepository
from app.services import google_sheets

log = logging.getLogger(__name__)


def default_name(ref: google_sheets.SheetRef) -> str:
    """A label for a sheet nobody named.

    The real title needs the Sheets API and a token this project does not have,
    so the honest fallback says what it is and enough of the key to tell two
    sheets apart. The user can link it again with a name to replace this.
    """
    return f"Google Sheet {ref.key[:8]}"


def upsert(
    db: Session,
    *,
    workspace_id: int,
    url: str,
    name: str | None = None,
) -> LinkedSheet:
    """Record this sheet as linked, or return the link that already exists.

    Importing the same sheet twice must not produce two rows, so the tab — key
    plus gid — is the identity rather than the URL text, which varies with how
    the link was copied.

    An existing row keeps its name unless a new one is given: the Import page
    links sheets as a side effect of importing them and has no name to offer,
    and letting that overwrite a name the user chose in Settings would rename
    their sheet every time they imported it.
    """
    ref = google_sheets.parse(url)
    repo = LinkedSheetRepository(db)

    sheet = repo.find(workspace_id, sheet_key=ref.key, gid=ref.gid)
    chosen = (name or "").strip()

    if sheet is None:
        return repo.add(
            LinkedSheet(
                workspace_id=workspace_id,
                name=chosen or default_name(ref),
                url=url.strip(),
                sheet_key=ref.key,
                gid=ref.gid,
            )
        )

    if chosen:
        sheet.name = chosen
    # The address as pasted most recently — the same tab reached by a newer
    # link shape is still worth showing back the way the user last gave it.
    sheet.url = url.strip()
    return sheet


def record(
    sheet: LinkedSheet,
    *,
    status: str,
    batch_id: int | None = None,
    when: datetime | None = None,
) -> None:
    """Note how the last import from this sheet went.

    Stored on the row because Settings lists every sheet with its outcome, and
    reaching the batch for each one would be a query per row for two columns.
    """
    sheet.last_status = status
    sheet.last_synced_at = when or utcnow()
    if batch_id is not None:
        sheet.last_batch_id = batch_id
