"""Per-user limits on the operations that cost more than a request.

Import, sync, rebuild and report generation all run on the single worker thread
that SQLite's one-writer limit already forces. Nothing about them looks like an
attack — they are ordinary authenticated work — so the login throttle, which
counts *failures*, never sees them. A user clicking Export four times because
the first felt slow, or a browser tab retrying in a loop, is the shape this
bounds.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.throttle import (
    RateLimiter,
    get_rate_limiter,
    rate_key,
    reset_rate_limiters,
)


@pytest.fixture(autouse=True)
def _fresh_limiters() -> None:
    reset_rate_limiters()


class TestAdmission:
    def test_it_allows_up_to_the_limit(self) -> None:
        limiter = RateLimiter(max_events=3, window_seconds=60)

        assert [limiter.admit("k", now=t).allowed for t in (0.0, 1.0, 2.0)] == [True] * 3

    def test_it_refuses_the_one_past_the_limit(self) -> None:
        limiter = RateLimiter(max_events=3, window_seconds=60)
        for t in (0.0, 1.0, 2.0):
            limiter.admit("k", now=t)

        assert limiter.admit("k", now=3.0).allowed is False

    def test_it_counts_successes_not_failures(self) -> None:
        """The difference from `LoginThrottle`. A successful import costs more
        than a rejected one, not less — the work is what is being charged for."""
        limiter = RateLimiter(max_events=2, window_seconds=60)

        limiter.admit("k", now=0.0)
        limiter.admit("k", now=1.0)

        assert limiter.admit("k", now=2.0).allowed is False

    def test_the_window_drains_rather_than_locking_out(self) -> None:
        """No lockout period: crossing the limit is a busy user, not an attack.
        A fixed lockout would punish a burst long after its load had gone."""
        limiter = RateLimiter(max_events=2, window_seconds=60)
        limiter.admit("k", now=0.0)
        limiter.admit("k", now=1.0)
        assert limiter.admit("k", now=30.0).allowed is False

        # The t=0 event ages out at t=60, freeing exactly one slot — and only
        # one: the t=1 event is still inside the window until t=61.
        assert limiter.admit("k", now=60.5).allowed is True
        assert limiter.admit("k", now=60.6).allowed is False

    def test_retry_after_points_at_the_slot_that_frees_next(self) -> None:
        limiter = RateLimiter(max_events=2, window_seconds=60)
        limiter.admit("k", now=0.0)
        limiter.admit("k", now=10.0)

        decision = limiter.admit("k", now=20.0)

        # The oldest event expires at 60; from 20 that is 40 seconds away.
        assert decision.retry_after_seconds == 40

    def test_retry_after_is_never_zero(self) -> None:
        """A client told to wait 0 seconds retries immediately and is refused
        again, which reads as a broken endpoint rather than a limit."""
        limiter = RateLimiter(max_events=1, window_seconds=60)
        limiter.admit("k", now=0.0)

        assert limiter.admit("k", now=59.9).retry_after_seconds >= 1

    def test_keys_are_independent(self) -> None:
        limiter = RateLimiter(max_events=1, window_seconds=60)
        limiter.admit("a", now=0.0)

        assert limiter.admit("b", now=0.0).allowed is True

    def test_check_does_not_consume_a_slot(self) -> None:
        limiter = RateLimiter(max_events=1, window_seconds=60)

        assert limiter.check("k", now=0.0).allowed is True
        assert limiter.check("k", now=0.0).allowed is True
        assert limiter.admit("k", now=0.0).allowed is True

    def test_a_refused_call_does_not_extend_the_window(self) -> None:
        """Otherwise hammering the endpoint would keep pushing the wait out,
        turning a rate limit into an indefinite ban."""
        limiter = RateLimiter(max_events=1, window_seconds=60)
        limiter.admit("k", now=0.0)
        for t in range(1, 50):
            limiter.admit("k", now=float(t))

        assert limiter.admit("k", now=61.0).allowed is True


class TestKeys:
    def test_it_separates_users(self) -> None:
        assert rate_key("import", workspace_id=1, user_id=1) != rate_key(
            "import", workspace_id=1, user_id=2
        )

    def test_it_separates_workspaces(self) -> None:
        """The contention is the worker thread and the SQLite writer, both per
        deployment — a key shared across tenants would let one spend another's."""
        assert rate_key("import", workspace_id=1, user_id=1) != rate_key(
            "import", workspace_id=2, user_id=1
        )

    def test_it_separates_operations(self) -> None:
        assert rate_key("import", workspace_id=1, user_id=1) != rate_key(
            "sync", workspace_id=1, user_id=1
        )


class TestTheRegistry:
    def test_each_operation_gets_its_own_limiter(self, settings) -> None:
        """A slow morning of imports must not stop anyone exporting."""
        assert get_rate_limiter(settings, "import") is not get_rate_limiter(settings, "report")

    def test_the_same_operation_gets_the_same_limiter(self, settings) -> None:
        assert get_rate_limiter(settings, "sync") is get_rate_limiter(settings, "sync")


class TestThroughTheApi:
    """The limit as a user meets it: a 429 with an envelope and a Retry-After."""

    @pytest.fixture(autouse=True)
    def _small_budget(self, signed_in: TestClient) -> None:
        """Prime the registry with a limiter tight enough to reach in a test.

        Depends on `signed_in` so it runs *after* it: building the app clears
        every cache, the rate limiters included, so priming first would be
        undone before the first request.

        `conftest` raises the limit for every other test — they assert things
        that have nothing to do with throttling and should not trip it. These
        tests need the opposite, and get it through the public registry: the
        first caller for an operation decides that limiter, so priming it here
        means the application's own `get_rate_limiter` finds this one.
        """
        reset_rate_limiters()
        get_rate_limiter(Settings(rate_limit_max_events=2, env="test"), "report")

    def _generate(self, client: TestClient) -> object:
        return client.post(
            "/api/reports",
            json={"kind": "inventory", "fmt": "csv", "range_option": "30", "top_only": False},
        )

    def test_a_burst_is_eventually_refused(self, signed_in: TestClient) -> None:
        codes = [self._generate(signed_in).status_code for _ in range(6)]

        assert 429 in codes, "no request was refused"
        # The early ones must succeed — a limit that refuses the first request
        # is a broken endpoint, not a limit.
        assert codes[0] in (200, 202)

    def test_the_refusal_carries_the_error_envelope(self, signed_in: TestClient) -> None:
        response = None
        for _ in range(6):
            response = self._generate(signed_in)
            if response.status_code == 429:
                break
        assert response is not None and response.status_code == 429

        body = response.json()["error"]
        assert body["code"] == "rate_limited"
        assert body["message"] and body["next"]
        # Says when to come back, so the client is not left guessing and looping.
        assert int(response.headers["Retry-After"]) >= 1

    def test_it_does_not_refuse_reads(self, signed_in: TestClient) -> None:
        """Only the expensive operations are limited. Listing the Export Centre
        is a cheap query and must stay usable while a limit is in force."""
        for _ in range(6):
            self._generate(signed_in)

        assert signed_in.get("/api/reports").status_code == 200
