"""Shared arithmetic and formatting for figures more than one module reports.

Percentages were being computed in four places — the dashboard query, the
insights derivations, the per-SKU table and the import summary — and they had
drifted: the same Shopify Sales % rendered as ``42.2`` on the Dashboard and
``42.19`` on the import summary, because one path rounded to one decimal and
another to two. The number was right in both; the disagreement was the bug, and
a reader has no way to tell a rounding difference from a calculation difference.

Both the arithmetic and the rendering live here for the same reason. A value
rounded to two places and then printed with one decimal is as inconsistent as
two different divisions, and that second kind of drift is harder to spot,
because every individual call site looks correct on its own.

This module is deliberately pure — no session, no models — so anything may
import it. That matters here: ``services.insights`` imports
``services.report_data``, which imports ``services.analytics``, so a helper
living in any of those three could not be shared by the other two without a
cycle.
"""

from __future__ import annotations

#: Every percentage this application reports carries two decimals: 0.64%, 0.12%,
#: 42.20%. One place was the previous convention for Shopify Sales % and it lost
#: real distinctions — a SKU at 0.09% of the store and one at 0.14% both render
#: as 0.1%, which turns the top of that ranking into a run of apparent ties.
PCT_PLACES = 2


def share_pct(part: float, whole: float, places: int = PCT_PLACES) -> float:
    """``part`` as a percentage of ``whole``, or 0.0 when there is no whole.

    Zero rather than a division by zero. A caller that needs to distinguish
    "nothing to divide by" from "genuinely zero" has the denominator to hand and
    can say so itself; every other caller is showing a portfolio figure where
    the zero contributes nothing.
    """
    return round(part / whole * 100, places) if whole else 0.0


def sales_pct(sku_sales: float, total_sales: float) -> float:
    """Shopify Sales %. **The one function**; two callers, two denominators.

    * **The KPI cards** divide by everything the Shopify store sold, matched to
      the sheet or not. That answers "how much of what we sold does this sheet
      account for", which only means something against the store's own total.
    * **The SKU Performance table** divides by the imported SKUs' own sales, so
      the column is a composition: every row is a share of one whole, no row can
      exceed 100%, and the column sums to 100%.

    They are different questions, and the two are not meant to agree — a row's
    share of the sheet is larger than its share of the store. What must not
    differ is the arithmetic, which is why both come through here.

    Nothing to divide by gives 0.00% rather than a division.
    """
    return share_pct(sku_sales, total_sales)


def format_pct(value: float) -> str:
    """A percentage as a bare number, for a spreadsheet cell: ``"10.00"``.

    No ``%`` sign, so a column of these can be averaged or charted. The column
    heading carries the unit instead.
    """
    return f"{value:.{PCT_PLACES}f}"


#: The character that escapes a LIKE metacharacter in :func:`like_contains`.
#: Backslash rather than the more common `!` because it is what a reader expects
#: and because no SKU in this system has ever contained one.
LIKE_ESCAPE = "\\"


def normalise_search(term: str | None) -> str | None:
    """The search box's text, folded once, or None when there is nothing to match.

    **The one place a search term is prepared.** The SKU table searched with SQL
    ``LIKE`` and the SKU Performance table with a Python substring test, so the
    same box answered two different questions: searching ``a_c`` returned 38 rows
    on one screen and 13 on the other, because ``_`` is a single-character
    wildcard in ``LIKE`` and a literal underscore in Python. Searching ``%``
    returned every row on one and none on the other.
    """
    if term is None:
        return None
    folded = term.strip().lower()
    return folded or None


def like_contains(term: str) -> str:
    """A ``LIKE`` pattern matching ``term`` **literally**, anywhere in the value.

    Every metacharacter is escaped, so what the user typed is what is matched.
    Pair with ``.like(pattern, escape=calc.LIKE_ESCAPE)`` — the escape character
    has to be declared at the call site or the database does not know to honour it.

    Escaping the escape first is not a stylistic ordering. Doing it last would
    re-escape the backslashes this function had just inserted.
    """
    escaped = term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    for meta in ("%", "_"):
        escaped = escaped.replace(meta, LIKE_ESCAPE + meta)
    return f"%{escaped}%"


def matches_search(value: str, term: str) -> bool:
    """Whether ``value`` contains ``term``, case-insensitively and literally.

    The in-memory counterpart of :func:`like_contains`, for the paths that filter
    rows Python already holds. Same question, same answer, so the two tables agree.

    Folds the term itself rather than trusting the caller to have done it. The
    SQL side folds once in :func:`normalise_search` before building a pattern;
    requiring the same discipline here would make an un-folded term silently
    match nothing, which is a worse failure than doing the work twice.
    """
    return term.strip().lower() in value.lower()
