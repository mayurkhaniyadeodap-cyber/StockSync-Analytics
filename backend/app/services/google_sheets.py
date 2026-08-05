"""Importing from a Google Sheet.

This module does exactly two things, and deliberately nothing else:

1. Turns the URL a user copies out of their browser into the URL Google serves a
   CSV from.
2. Translates the download's failures into language about *sheets* — "make it
   viewable by anyone with the link" is actionable; "that link returned a web
   page" is not, even though it is what technically happened.

The download itself is ``import_url.fetch_csv``, unchanged, including its address
guard and size cap. The import is ``imports.run_file_import``, unchanged. There is
no parsing, validation or upsert here — a Google Sheet import *is* a CSV import
that arrived by a different door, and the only thing Import History records
differently is the method.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs, urlparse

from app.config import Settings
from app.core.errors import AppError
from app.services import import_url

log = logging.getLogger(__name__)

#: The document key in a normal sharing link: /spreadsheets/d/<id>/edit#gid=0
#: A `u/0/` prefix appears when the user has several Google accounts signed in.
_SHEET_ID = re.compile(r"/spreadsheets/(?:u/\d+/)?d/([a-zA-Z0-9_-]{8,})")

#: The key in a *published* link: /spreadsheets/d/e/<key>/pubhtml. A different
#: namespace with a different export path, so it cannot share the branch above.
_PUBLISHED_KEY = re.compile(r"/spreadsheets/(?:u/\d+/)?d/e/([a-zA-Z0-9_-]{8,})")

#: Which tab. A sheet's gid is not guaranteed to be 0 — deleting the first tab
#: leaves the next one with whatever id it was created with — so an absent gid is
#: left out of the export URL entirely rather than defaulted. Google then serves
#: the first visible sheet, which is what the user is looking at.
_GID: Final = "gid"

_HOSTS: Final = frozenset({"docs.google.com", "www.docs.google.com"})


class NotAGoogleSheetUrlError(AppError):
    code = "not_a_google_sheet_url"
    status_code = 422
    message = "That doesn't look like a Google Sheet link."
    next_step = "Open the sheet, copy the address from your browser, and paste the whole thing."


class SheetNotPublicError(AppError):
    """Google refused the export: sign-in wall, or a redirect to one."""

    code = "sheet_not_public"
    status_code = 422
    message = (
        "This Google Sheet isn't publicly accessible. Please make it viewable by "
        "anyone with the link or upload it as a CSV file."
    )
    next_step = "In Google Sheets: Share → General access → Anyone with the link → Viewer."


@dataclass(frozen=True)
class SheetRef:
    """A sheet link, taken apart.

    ``key`` and ``gid`` together identify one tab of one document, which is what
    makes two links to the same tab recognisable as the same sheet however they
    were copied. ``gid`` is the empty string when the link names no tab.
    """

    export: str
    key: str
    gid: str


def export_url(url: str) -> tuple[str, str]:
    """Translate a sheet link into its CSV export URL. Returns ``(url, key)``.

    ``key`` is the document id, used to name the row in Import History.
    """
    ref = parse(url)
    return ref.export, ref.key


def parse(url: str) -> SheetRef:
    """Take a sheet link apart. The single place link shapes are understood.

    Three shapes reach this function in practice:

    * a normal sharing or editing link — ``/d/<id>/edit#gid=123``
    * a published-to-web link — ``/d/e/<key>/pubhtml``, a different namespace
      that exports from ``/pub?output=csv`` rather than ``/export?format=csv``
    * an export URL the user already built, which is passed through as-is
    """
    text = (url or "").strip()
    if not text:
        raise NotAGoogleSheetUrlError

    parsed = urlparse(text if "://" in text else f"https://{text}")
    if parsed.scheme.lower() not in ("http", "https") or parsed.hostname not in _HOSTS:
        raise NotAGoogleSheetUrlError(detail={"received": text[:200]})

    gid = _gid_from(parsed.query, parsed.fragment)

    # Published links first: their key also matches the general pattern, so
    # testing in the other order would build an export URL Google 404s on.
    published = _PUBLISHED_KEY.search(parsed.path)
    if published is not None:
        key = published.group(1)
        target = f"https://docs.google.com/spreadsheets/d/e/{key}/pub?output=csv"
        return SheetRef(f"{target}&{_GID}={gid}" if gid else target, key, gid or "")

    match = _SHEET_ID.search(parsed.path)
    if match is None:
        raise NotAGoogleSheetUrlError(detail={"received": text[:200]})

    key = match.group(1)
    target = f"https://docs.google.com/spreadsheets/d/{key}/export?format=csv"
    return SheetRef(f"{target}&{_GID}={gid}" if gid else target, key, gid or "")


def _gid_from(query: str, fragment: str) -> str | None:
    """The tab id, from ``?gid=`` or from ``#gid=``.

    The fragment is where a browser puts it — ``/edit#gid=123`` — and a fragment
    is never sent to a server, so it has to be moved into the query to survive.
    """
    for source in (fragment, query):
        values = parse_qs(source).get(_GID)
        if values and values[0].isdigit():
            return values[0]
    return None


def fetch(settings: Settings, url: str) -> tuple[str, bytes]:
    """Download a sheet as CSV. Returns ``(filename, raw)`` for the importer.

    The filename carries the first characters of the document key: two imports
    from two different sheets have to be distinguishable in Import History, and
    the sheet's *title* is not available without the Sheets API and a token, which
    this deliberately does not need.
    """
    target, key = export_url(url)
    log.info("google sheet export: %s", target)

    try:
        _, raw = import_url.fetch_csv(settings, target)
    except import_url.ImportUrlHttpError as exc:
        # Three ways Google says "no", all one problem to the person asking:
        #
        #   401/403  — the direct refusal
        #   redirect exhaustion — bounced into the sign-in flow. A *public* export
        #     from this host redirects once at most, so a chain longer than the
        #     budget means auth, not a misconfigured link.
        #
        # A 404 is deliberately not folded in: a sheet that does not exist is a
        # different mistake from one that is private, and saying "make it public"
        # about a deleted sheet sends the user to the wrong place.
        if exc.detail.get("status") in (401, 403) or "max_redirects" in exc.detail:
            raise SheetNotPublicError from exc
        raise
    except import_url.ImportUrlNotAFileError as exc:
        # Google answers a private sheet by redirecting to a sign-in page, so the
        # download succeeds and returns HTML. From the user's side that is the
        # same problem as a 403, and it gets the same sentence.
        raise SheetNotPublicError from exc

    return f"google-sheet-{key[:12]}.csv", raw
