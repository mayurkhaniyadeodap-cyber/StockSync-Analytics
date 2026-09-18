"""`?complaints=` — whether complaint figures follow the date range.

The Dashboard asks for `total` and the Analytics pages do not, so both answers
have to be right for the same workspace at the same moment. What makes this
worth its own file is that the two are only distinguishable when a SKU *has*
dates and some of them fall outside the window — which is precisely the case a
fixture is easy to get wrong.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import COMPLAINT_COLUMNS
from tests.test_analytics_api import complain, import_sheet, rebuild, sheet_row

OVERVIEW = "/api/analytics/overview"
PERFORMANCE = "/api/analytics/performance"
INSIGHTS = "/api/analytics/insights"

#: Ten categories; only the first carries a count in these fixtures.
DEFECT_ONLY = (5, 0, 0, 0, 0, 0, 0, 0, 0, 0)


@pytest.fixture
def dated(signed_in: TestClient) -> TestClient:
    """One SKU whose sheet says 12 complaints, with 10 of them dated.

    Five land inside a 7-day window and five outside it, so the three possible
    answers are all different numbers:

    * the sheet's own total — 12, whatever the range
    * dated, last 7 days — 5
    * dated, last 90 days — 10

    That last pair is what makes the test meaningful: a fixture where the window
    happens to capture everything cannot tell `range` from `total`.
    """
    import_sheet(
        signed_in,
        [sheet_row("DD-1001", 100, total_orders=200, complaints=(12, 0, 0, 0, 0, 0, 0, 0, 0, 0))],
    )
    complain(signed_in, [("DD-1001", DEFECT_ONLY, 1), ("DD-1001", DEFECT_ONLY, 40)])
    rebuild(signed_in)
    return signed_in


def complaints_on(client: TestClient, url: str) -> int:
    body = client.get(url).json()
    return int(body["kpis"]["total_complaints"])


class TestTheDashboardAsksForTheWholeRecord:
    def test_total_ignores_the_range(self, dated: TestClient) -> None:
        """The sheet's own figure, whatever window is asked for."""
        assert complaints_on(dated, f"{OVERVIEW}?days=7&complaints=total") == 12
        assert complaints_on(dated, f"{OVERVIEW}?days=90&complaints=total") == 12

    def test_insights_answers_the_same_way(self, dated: TestClient) -> None:
        """Both endpoints the Dashboard reads have to agree, or its cards and
        its complaint breakdown would report different totals."""
        assert complaints_on(dated, f"{INSIGHTS}?days=7&complaints=total") == 12
        assert complaints_on(dated, f"{INSIGHTS}?days=90&complaints=total") == 12

    def test_the_table_agrees_with_the_cards(self, dated: TestClient) -> None:
        body = dated.get(f"{PERFORMANCE}?days=7&complaints=total&limit=50").json()
        assert body["rows"][0]["total_complaints"] == 12

    def test_the_scope_note_reports_no_filtering(self, dated: TestClient) -> None:
        """`filtered_by_date` is what draws the "complaints follow the range"
        banner. Asking for totals must turn it off, or the Dashboard would
        caveat a figure that no longer needs the caveat."""
        body = dated.get(f"{OVERVIEW}?days=7&complaints=total").json()
        assert body["kpis"]["complaint_scope"]["filtered_by_date"] is False


class TestAnalyticsKeepsTheWindow:
    def test_range_is_still_the_default(self, dated: TestClient) -> None:
        """No parameter means the long-standing behaviour, so nothing that was
        already calling these endpoints changed."""
        assert complaints_on(dated, f"{OVERVIEW}?days=7") == 5
        assert complaints_on(dated, f"{OVERVIEW}?days=90") == 10

    def test_range_asked_for_explicitly(self, dated: TestClient) -> None:
        assert complaints_on(dated, f"{INSIGHTS}?days=7&complaints=range") == 5
        assert complaints_on(dated, f"{INSIGHTS}?days=90&complaints=range") == 10

    def test_the_scope_note_still_fires(self, dated: TestClient) -> None:
        body = dated.get(f"{OVERVIEW}?days=7&complaints=range").json()
        assert body["kpis"]["complaint_scope"]["filtered_by_date"] is True


class TestTheBasisTouchesNothingElse:
    def test_shopify_sales_still_follow_the_range(self, dated: TestClient) -> None:
        """The whole point of the change is that it moves complaints and leaves
        the sales window alone."""
        for basis in ("range", "total"):
            body = dated.get(f"{OVERVIEW}?days=30&complaints={basis}").json()
            assert body["kpis"]["days"] == 30

    def test_the_snapshot_figures_are_unmoved(self, dated: TestClient) -> None:
        narrow = dated.get(f"{OVERVIEW}?days=7&complaints=total").json()["kpis"]
        wide = dated.get(f"{OVERVIEW}?days=90&complaints=total").json()["kpis"]

        for field in ("total_skus", "total_quantity", "total_orders", "low_stock"):
            assert narrow[field] == wide[field], field

    def test_an_unknown_basis_is_refused(self, dated: TestClient) -> None:
        """A typo must not silently fall back to one of the two answers."""
        assert dated.get(f"{OVERVIEW}?days=7&complaints=lifetime").status_code == 422


def test_an_aggregated_only_workspace_reads_the_same_either_way(
    signed_in: TestClient,
) -> None:
    """A sheet with no dated rows has one answer, and both bases give it.

    This is the path `whole_record()` reuses, so it is worth pinning that the
    two agree where they must — otherwise the flag would look correct only
    because the fixture always has dates.
    """
    import_sheet(
        signed_in,
        [sheet_row("DD-2001", 40, total_orders=40, complaints=(7, 0, 0, 0, 0, 0, 0, 0, 0, 0))],
    )
    rebuild(signed_in)

    assert complaints_on(signed_in, f"{OVERVIEW}?days=7&complaints=range") == 7
    assert complaints_on(signed_in, f"{OVERVIEW}?days=7&complaints=total") == 7


def test_every_category_column_follows_the_basis(dated: TestClient) -> None:
    """Not just the summed total: the per-category columns the table renders
    come from the same resolution and must move with it."""
    field = COMPLAINT_COLUMNS[0][0]

    windowed = dated.get(f"{PERFORMANCE}?days=7&complaints=range&limit=50").json()
    whole = dated.get(f"{PERFORMANCE}?days=7&complaints=total&limit=50").json()

    assert windowed["rows"][0]["complaints"][field] == 5
    assert whole["rows"][0]["complaints"][field] == 12
