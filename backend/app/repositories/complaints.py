"""Complaint counts for a window, from files that may or may not carry dates.

**The one place that decides whether a complaint figure follows the date range.**

Two upload formats reach this system and only one of them has dates on it:

* the **aggregated sheet** — one row per SKU with running complaint totals, and
  no date column anywhere. Nothing about it can be placed in a window.
* the **complaint export** — one row per complaint, carrying the day it
  happened. ``sku_daily_complaints`` is written from it at import.

Neither is more correct than the other and a workspace can hold both, because
each import replaces only the SKUs it mentions. So the question is not asked of
the workspace but of each SKU: *do we have dates for this one?*

**Dated records win, per SKU, absolutely.** Once ``sku_daily_complaints`` holds
anything for a SKU it is that SKU's whole record: the window is summed from it,
and the aggregated columns on ``inventory_items`` are not consulted for that SKU
again — not for the figure, not for the note. A SKU without dated rows reports
the totals its sheet stated, unchanged by the range, and :class:`ComplaintScope`
carries the sentence that says so.

The two are never mixed for one SKU. Adding a dated figure to an aggregated one
would double-count, and preferring the aggregate would silently discard the
dates the user imported.

The rule lives here rather than in either caller because there are two — the
Analytics read path and the Dashboard's own table — and a figure computed twice
is a figure that eventually disagrees with itself. That has already happened
once in this codebase, to Shopify Sales %.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import COMPLAINT_COLUMNS, InventoryItem, SkuDailyComplaint


@dataclass(frozen=True)
class ComplaintScope:
    """Whether the complaint figures on screen answer to the date range.

    Returned alongside the counts so a screen never has to work this out from
    the numbers — which it could not do anyway, since "0 complaints this month"
    and "complaints we cannot place in a month" look identical from outside.

    **Counts only, no sentence.** This carried a `note` for a while and the
    client stopped reading it: the wording depends on which of three states the
    workspace is in and needs the counts formatted for the locale, both of which
    belong to the screen. A string here that nothing renders is a string that
    quietly goes stale — it kept claiming no Complaint Date column was provided
    on a workspace with 308 dated SKUs.
    """

    #: SKUs whose complaints were imported with dates and so follow the range.
    dated_skus: int = 0
    #: SKUs whose complaints are the sheet's totals, the same in every range.
    undated_skus: int = 0
    #: Complaints no range can filter — every complaint belonging to a SKU with
    #: no dated records at all. A SKU that has dated records contributes nothing
    #: here whatever its aggregated columns say, because those columns are not
    #: its source any more.
    undated_complaints: int = 0

    @property
    def filtered_by_date(self) -> bool:
        """True when at least some complaints follow the selected range."""
        return self.dated_skus > 0


def scope_from(rows: Iterable[tuple[bool, int]]) -> ComplaintScope:
    """Count the scope from ``(this SKU has dates, complaints no range can hold)``.

    **The one implementation**, deliberately taking a shape both callers can
    produce: this module builds the pairs from the sheet's tallies, and
    ``services.insights`` builds them from facts it already holds, so neither
    has to re-read to answer the same question. A second copy of this counting
    is exactly how the note would end up saying one thing on the Dashboard and
    another on SKU Performance.

    A SKU with dates always counts, even with nothing in this window — having no
    complaints *this month* is a real answer, not an absence of data. A SKU
    without dates only counts once it has a complaint to be undated about.
    """
    dated = undated = unfilterable_total = 0
    for is_dated, unfilterable in rows:
        if is_dated:
            dated += 1
        elif unfilterable > 0:
            undated += 1
        unfilterable_total += unfilterable
    return ComplaintScope(
        dated_skus=dated,
        undated_skus=undated,
        undated_complaints=unfilterable_total,
    )


_FIELDS = tuple(attribute for attribute, _ in COMPLAINT_COLUMNS)


def _zero() -> dict[str, int]:
    return dict.fromkeys(_FIELDS, 0)


@dataclass(frozen=True)
class ComplaintWindow:
    """One workspace's complaints, resolved for one window.

    Built by :func:`read`. Holds no session: the two queries happen once, up
    front, and every SKU is then answered from memory — the same reason
    ``SkuFactRepository`` folds its facts in Python rather than querying per
    widget.
    """

    #: ``sku_normalized -> {category: count}`` inside the window. Only SKUs with
    #: dated complaints appear; a dated SKU with none in this window is absent
    #: and resolves to zeroes, which is the correct answer for it.
    windowed: Mapping[str, Mapping[str, int]]
    #: ``sku_normalized -> every dated complaint on record``, ignoring the
    #: window. This is what says a SKU *has* dates — the windowed sum cannot,
    #: because a dated SKU with nothing in range sums to zero just like an
    #: undated one.
    dated_total: Mapping[str, int]

    def is_dated(self, sku_normalized: str) -> bool:
        """Whether this SKU's complaints can be placed in a range at all."""
        return self.dated_total.get(sku_normalized, 0) > 0

    def counts(self, sku_normalized: str, totals: Mapping[str, int]) -> dict[str, int]:
        """This SKU's complaints for the window, by category.

        ``totals`` is the sheet's own tally from ``inventory_items``, returned
        unchanged when the SKU has no dates — the imported figure is the whole
        record for it, and hiding it behind a range it never had would replace a
        real number with a wrong one.
        """
        if not self.is_dated(sku_normalized):
            return dict(totals)
        return dict(self.windowed.get(sku_normalized, _zero()))

    def unfilterable(self, sku_normalized: str, totals: Mapping[str, int]) -> int:
        """Complaints this SKU has that no range can include.

        **All of them when the SKU has no dated records, and none when it has.**
        Once a SKU is dated, ``sku_daily_complaints`` is its whole record and the
        aggregated columns are not consulted for it at any point — not for the
        figure, and not for this.

        Reporting the difference instead was the earlier behaviour and it was
        wrong twice over. It consulted the aggregated source for a dated SKU,
        which the rule forbids; and because the note fires on any unfilterable
        complaint, two stray blank date cells in an otherwise fully dated import
        kept it on screen permanently — on the live workspace, 2 complaints out
        of 5,458 came from that path and would have outlived every fix to the
        other 5,456.

        The cost is real and worth stating: a complaint row whose own date cell
        was unreadable reaches ``inventory_items`` and no window, so it is not
        shown anywhere. That is a defect in the source file, and the import
        already reports it — ``ParsedRow.undated_complaints`` counts those rows
        at the point they are read, which is where it can be acted on.
        """
        if self.is_dated(sku_normalized):
            return 0
        return sum(totals.values())

    def scope(self, items: Iterable[tuple[str, Mapping[str, int]]]) -> ComplaintScope:
        """Summarise the workspace from ``(sku_normalized, category totals)``."""
        return scope_from(
            (self.is_dated(sku), self.unfilterable(sku, totals)) for sku, totals in items
        )


@dataclass(frozen=True)
class ResolvedComplaints:
    """A whole workspace's complaints for one window, already decided.

    For the callers that do not hold the inventory rows themselves — the
    Dashboard's card and its table. ``SkuFactRepository`` has the rows in hand
    from its own join and uses :class:`ComplaintWindow` directly; both go
    through the same :meth:`ComplaintWindow.counts`, so the two cannot part
    company over what a SKU's complaints are.
    """

    by_sku: Mapping[str, dict[str, int]]
    #: The workspace total, summed from ``by_sku`` rather than from SQL, so the
    #: card above the table is the addition of the column in it.
    total: int
    scope: ComplaintScope


def resolve(db: Session, workspace_id: int, *, since: date, until: date) -> ResolvedComplaints:
    """Read the sheet's tallies and the dated rows, and decide between them."""
    window = read(db, workspace_id, since=since, until=until)
    rows = db.execute(
        select(
            InventoryItem.sku_normalized,
            *(getattr(InventoryItem, attribute) for attribute, _ in COMPLAINT_COLUMNS),
        ).where(InventoryItem.workspace_id == workspace_id)
    ).all()

    totals = {
        row[0]: {
            attribute: int(row[index + 1] or 0)
            for index, (attribute, _) in enumerate(COMPLAINT_COLUMNS)
        }
        for row in rows
    }
    by_sku = {sku: window.counts(sku, tally) for sku, tally in totals.items()}
    return ResolvedComplaints(
        by_sku=by_sku,
        total=sum(sum(counts.values()) for counts in by_sku.values()),
        scope=window.scope(totals.items()),
    )


def read(db: Session, workspace_id: int, *, since: date, until: date) -> ComplaintWindow:
    """Two grouped reads: the window's counts, and who has dates at all.

    Both are answered from ``ix_sku_daily_complaints_cover`` without touching
    the table. A workspace that has only ever imported aggregated sheets has no
    rows here, so both come back empty and every SKU falls through to its
    imported totals.
    """
    windowed_rows = db.execute(
        select(
            SkuDailyComplaint.sku_normalized,
            *(func.sum(getattr(SkuDailyComplaint, field)) for field in _FIELDS),
        )
        .where(
            SkuDailyComplaint.workspace_id == workspace_id,
            SkuDailyComplaint.complaint_date >= since,
            SkuDailyComplaint.complaint_date <= until,
        )
        .group_by(SkuDailyComplaint.sku_normalized)
    ).all()

    dated_rows = db.execute(
        select(
            SkuDailyComplaint.sku_normalized,
            func.sum(SkuDailyComplaint.total_complaints),
        )
        .where(SkuDailyComplaint.workspace_id == workspace_id)
        .group_by(SkuDailyComplaint.sku_normalized)
    ).all()

    return ComplaintWindow(
        windowed={
            row[0]: {field: int(row[index + 1] or 0) for index, field in enumerate(_FIELDS)}
            for row in windowed_rows
        },
        dated_total={row[0]: int(row[1] or 0) for row in dated_rows},
    )
