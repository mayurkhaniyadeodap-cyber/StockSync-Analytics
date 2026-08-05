"""The production-audit fixes, each asserted against the behaviour that was wrong.

Every class here corresponds to one finding. The docstrings carry the evidence
that made it a finding, so a future reader can tell a deliberate rule from an
accident and knows what breaking the test would re-open.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.core import calc
from app.core.throttle import LoginThrottle, keys_for, reset_login_throttle
from app.core.window import window_for
from app.db import session as session_module
from app.models import SyncRun
from app.models.localtime import local_day, offset_minutes
from tests.conftest import TEST_EMAIL, TEST_PASSWORD

# Re-exported so the concurrency test can use the same scripted store and stored
# credential the sync tests do, rather than a second copy that would drift.
from tests.test_sync_api import connected, shopify  # noqa: F401

LOGIN = "/api/auth/login"


@pytest.fixture
def signed_out(api: TestClient) -> TestClient:
    """A client with a seeded user and no session. Reads better than `api` here."""
    return api


@pytest.fixture
def seeded_skus(signed_in: TestClient) -> TestClient:
    """A workspace holding SKUs whose names exercise the LIKE metacharacters."""
    import io

    body = "SKU,Total Qty.,Total Orders.\n" + "".join(
        f"{sku},10,2\n" for sku in ("DD-1", "DD-2", "abc", "a_c", "50%off", "kitchen-towel")
    )
    response = signed_in.post(
        "/api/imports/upload",
        files={"file": ("sheet.csv", io.BytesIO(body.encode()), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return signed_in


@pytest.fixture
def connected_ws(connected: TestClient) -> int:
    """The workspace id of a client that has a stored Shopify credential."""
    return 1


def _production(**overrides: object) -> dict[str, object]:
    """The minimum a production Settings needs, before the field under test."""
    return {
        "env": "production",
        "jwt_secret": "p" * 48,
        "encryption_key": "H8vJ3kQm7pR2wX5yZ1aB4cD6eF9gI0jK2lM3nO5pQ7s=",
        **overrides,
    }


# ============================================================ H2
class TestSecureCookiesAreEnforcedInProduction:
    """A production server that sends session cookies in clear must not start.

    `jwt_secret` and `encryption_key` were already enforced; this one was not,
    which made it the setting a deployment could get wrong by omission. The cost
    is the whole session, readable by anyone on the network path.
    """

    def test_production_without_secure_cookies_refuses_to_start(self) -> None:
        with pytest.raises(ValueError, match="STOCKSYNC_COOKIE_SECURE"):
            Settings(**_production(cookie_secure=False))  # type: ignore[arg-type]

    def test_production_with_secure_cookies_is_fine(self) -> None:
        assert Settings(**_production(cookie_secure=True)).cookie_secure is True  # type: ignore[arg-type]

    def test_the_override_is_explicit_and_works(self) -> None:
        """TLS terminating on a trusted hop is a real deployment, not a mistake.

        What it may not be is the default: turning it off has to be typed.
        """
        settings = Settings(  # type: ignore[arg-type]
            **_production(cookie_secure=False, allow_insecure_cookies=True)
        )
        assert settings.cookie_secure is False

    @pytest.mark.parametrize("env", ["development", "test"])
    def test_other_environments_are_unaffected(self, env: str) -> None:
        assert Settings(env=env, cookie_secure=False).cookie_secure is False  # type: ignore[arg-type]


# ============================================================ M3
class TestSecurityHeaders:
    """Cookie authentication makes framing and MIME sniffing real risks."""

    def test_every_response_carries_the_headers(self, client: TestClient, healthy_db: None) -> None:
        headers = client.get("/api/health").headers

        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "same-origin"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_an_error_response_carries_them_too(self, client: TestClient) -> None:
        """An attacker reaches for the paths that fail, so these cannot be
        attached only on the success path."""
        response = client.get("/api/analytics/kpis")

        assert response.status_code == 401
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_hsts_is_absent_without_secure_cookies(
        self, client: TestClient, healthy_db: None
    ) -> None:
        """Pinning a browser to HTTPS from a server that answers on HTTP locks
        users out until max-age expires, and nothing can undo it early."""
        assert "Strict-Transport-Security" not in client.get("/api/health").headers

    def test_hsts_is_present_when_tls_is_in_use(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.db.session import DatabaseStatus
        from app.main import create_app

        monkeypatch.setattr(
            "app.main.ping_database", lambda: DatabaseStatus(ok=True, latency_ms=1.0)
        )
        secure = Settings(
            env="test", database_url="sqlite+pysqlite:///:memory:", cookie_secure=True
        )
        with TestClient(create_app(secure)) as secured:
            header = secured.get("/api/health").headers["Strict-Transport-Security"]

        assert "max-age=" in header
        assert "includeSubDomains" in header


# ============================================================ H3
class TestLoginThrottleUnit:
    """The counter itself, driven with an injected clock so nothing sleeps."""

    def throttle(self) -> LoginThrottle:
        return LoginThrottle(max_attempts=3, window_seconds=60, lockout_seconds=300)

    def test_failures_under_the_limit_are_allowed(self) -> None:
        t = self.throttle()
        for _ in range(2):
            t.record_failure(["user:a"], now=0.0)

        assert t.check(["user:a"], now=0.0).allowed is True

    def test_the_limit_locks_out(self) -> None:
        t = self.throttle()
        for _ in range(3):
            t.record_failure(["user:a"], now=0.0)

        decision = t.check(["user:a"], now=0.0)
        assert decision.allowed is False
        assert decision.retry_after_seconds == 300

    def test_the_lockout_expires(self) -> None:
        t = self.throttle()
        for _ in range(3):
            t.record_failure(["user:a"], now=0.0)

        assert t.check(["user:a"], now=301.0).allowed is True

    def test_failures_outside_the_window_do_not_accumulate(self) -> None:
        """Two mistypes last week must not leave someone one attempt from a lockout."""
        t = self.throttle()
        t.record_failure(["user:a"], now=0.0)
        t.record_failure(["user:a"], now=1.0)
        t.record_failure(["user:a"], now=500.0)  # the first two have aged out

        assert t.check(["user:a"], now=500.0).allowed is True

    def test_success_clears_the_count(self) -> None:
        t = self.throttle()
        t.record_failure(["user:a"], now=0.0)
        t.record_failure(["user:a"], now=0.0)
        t.record_success(["user:a"])
        t.record_failure(["user:a"], now=0.0)

        assert t.check(["user:a"], now=0.0).allowed is True

    def test_keys_are_independent(self) -> None:
        """One locked account must not lock out an unrelated one."""
        t = self.throttle()
        for _ in range(3):
            t.record_failure(["user:a"], now=0.0)

        assert t.check(["user:b"], now=0.0).allowed is True

    def test_either_key_can_trigger_the_refusal(self) -> None:
        """Per-account alone lets one client sweep many addresses; per-client
        alone lets one address be ground down from anywhere."""
        t = self.throttle()
        for _ in range(3):
            t.record_failure(["ip:1.2.3.4"], now=0.0)

        assert t.check(["user:fresh", "ip:1.2.3.4"], now=0.0).allowed is False

    def test_pruning_drops_only_what_is_finished(self) -> None:
        """The dict is keyed by attacker-controlled input, so it must not grow."""
        t = self.throttle()
        t.record_failure(["user:old"], now=0.0)
        for _ in range(3):
            t.record_failure(["user:locked"], now=0.0)
        assert t.tracked == 2

        # At t=100 'old' has aged out of the 60s window and holds no lock, while
        # 'locked' is locked until 300. Pruning later than that would drop both,
        # correctly — an expired lockout is finished business.
        t.prune(now=100.0)

        assert t.tracked == 1
        assert t.check(["user:locked"], now=100.0).allowed is False

    def test_pruning_drops_a_lockout_once_it_has_expired(self) -> None:
        t = self.throttle()
        for _ in range(3):
            t.record_failure(["user:locked"], now=0.0)

        t.prune(now=400.0)

        assert t.tracked == 0

    def test_keys_fold_the_address_the_way_the_lookup_does(self) -> None:
        assert keys_for("Admin@Deodap.in", "1.2.3.4") == ["user:admin@deodap.in", "ip:1.2.3.4"]

    def test_a_missing_client_address_still_yields_the_account_key(self) -> None:
        assert keys_for("a@b.co", None) == ["user:a@b.co"]


class TestLoginThrottleEndToEnd:
    @pytest.fixture(autouse=True)
    def _fresh(self) -> None:
        # A process-wide singleton; a test that inherited another's counters
        # would pass or fail on ordering.
        reset_login_throttle()

    def test_repeated_wrong_passwords_are_eventually_refused(self, signed_out: TestClient) -> None:
        """Before this, twelve wrong passwords completed in 1.14 s, all 401,
        nothing refused, and the correct password worked immediately after."""
        seen = [
            signed_out.post(LOGIN, json={"email": TEST_EMAIL, "password": f"wrong{i}"}).status_code
            for i in range(12)
        ]

        assert 429 in seen, "an unlimited run of wrong passwords is the defect"
        assert seen[0] == 401, "the first attempt is a normal refusal, not a throttle"

    def test_the_refusal_says_when_to_come_back(self, signed_out: TestClient) -> None:
        for _ in range(12):
            response = signed_out.post(LOGIN, json={"email": TEST_EMAIL, "password": "no"})
            if response.status_code == 429:
                break

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        body = response.json()["error"]
        assert body["code"] == "too_many_attempts"
        # How long to wait, not how many attempts remain: a countdown of
        # remaining attempts tells an attacker exactly how hard they may push.
        assert "Try again in" in body["next"]

    def test_a_correct_password_still_works_under_the_limit(self, signed_out: TestClient) -> None:
        signed_out.post(LOGIN, json={"email": TEST_EMAIL, "password": "wrong"})

        response = signed_out.post(LOGIN, json={"email": TEST_EMAIL, "password": TEST_PASSWORD})

        assert response.status_code == 200

    def test_signing_in_clears_the_suspicion(self, signed_out: TestClient) -> None:
        """Someone who mistypes twice and then gets it right starts clean."""
        for _ in range(2):
            signed_out.post(LOGIN, json={"email": TEST_EMAIL, "password": "wrong"})
        assert (
            signed_out.post(
                LOGIN, json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
            ).status_code
            == 200
        )

        after = [
            signed_out.post(LOGIN, json={"email": TEST_EMAIL, "password": "wrong"}).status_code
            for _ in range(3)
        ]

        assert after == [401, 401, 401], "the counter should have restarted"


# ============================================================ M1
class TestSearchMeansOneThing:
    """`%` and `_` are LIKE metacharacters and were interpolated raw.

    Searching `a_c` returned 38 rows on the Dashboard and 13 on SKU Performance;
    `%` returned every row on one and none on the other. Same box, two answers.
    """

    @pytest.mark.parametrize(
        ("term", "value", "expected"),
        [
            ("a_c", "abc", False),  # `_` is a literal underscore, not "any char"
            ("a_c", "a_c", True),
            ("%", "anything", False),  # `%` matches nothing unless it is present
            ("%", "50%off", True),
            ("kitchen", "2503_small_kitchen_towel", True),
            ("KITCHEN", "2503_small_kitchen_towel", True),  # folded
            ("  kitchen  ", "2503_small_kitchen_towel", True),  # trimmed
            ("\\", "back\\slash", True),  # the escape character itself
        ],
    )
    def test_the_in_memory_side_matches_literally(
        self, term: str, value: str, expected: bool
    ) -> None:
        assert calc.matches_search(value, term) is expected

    @pytest.mark.parametrize(
        ("term", "pattern"),
        [
            ("abc", "%abc%"),
            ("a_c", "%a\\_c%"),
            ("50%", "%50\\%%"),
            ("a\\b", "%a\\\\b%"),  # the escape is escaped first, or it re-escapes itself
        ],
    )
    def test_the_sql_side_escapes_every_metacharacter(self, term: str, pattern: str) -> None:
        assert calc.like_contains(term) == pattern

    @pytest.mark.parametrize("term", ["", "   ", None])
    def test_an_empty_term_is_no_filter_at_all(self, term: str | None) -> None:
        assert calc.normalise_search(term) is None

    def test_both_sides_agree_through_the_api(self, seeded_skus: TestClient) -> None:
        """The property that matters: one term, two endpoints, one answer."""
        for term in ("a_c", "%", "dd", "DD-1"):
            table = seeded_skus.get(f"/api/analytics/skus?days=30&search={term}").json()
            performance = seeded_skus.get(
                f"/api/analytics/performance?days=30&search={term}"
            ).json()

            assert table["total"] == performance["total"], f"disagreed on {term!r}"


# ============================================================ M4
class TestWorkspaceTimezone:
    """17.7% of this store's orders fall between 00:00 and 05:30 UTC, which is
    the previous day in India. Bucketing on the UTC day files them one day late."""

    def test_the_offset_comes_from_the_zone(self) -> None:
        assert offset_minutes("Asia/Kolkata") == 330
        assert offset_minutes("UTC") == 0

    def test_an_unknown_zone_falls_back_to_utc(self) -> None:
        """A typo in a settings field must not take the dashboard down."""
        assert offset_minutes("Not/AZone") == 0
        assert offset_minutes(None) == 0
        assert offset_minutes("") == 0

    def test_the_window_ends_on_the_workspaces_today(self) -> None:
        """At 23:00 UTC it is already tomorrow in Kolkata."""
        at_utc = window_for(30, offset_minutes=0)
        at_ist = window_for(30, offset_minutes=330)

        assert (at_ist[1] - at_utc[1]).days in (0, 1)
        assert (at_ist[1] - at_ist[0]).days == 29, "30 days, inclusive of both ends"

    def test_zero_offset_is_the_previous_behaviour(self) -> None:
        until = datetime.now(UTC).date()
        assert window_for(30, offset_minutes=0)[1] == until

    def test_the_day_expression_shifts_on_sqlite(self) -> None:
        from sqlalchemy.dialects import sqlite

        from app.models import Order

        shifted = str(local_day(Order.processed_at, 330).compile(dialect=sqlite.dialect()))
        assert "+330 minutes" in shifted

    def test_a_zero_offset_compiles_to_a_plain_date(self) -> None:
        """A UTC workspace must produce exactly the SQL it produced before."""
        from sqlalchemy.dialects import sqlite

        from app.models import Order

        assert "minutes" not in str(
            local_day(Order.processed_at, 0).compile(dialect=sqlite.dialect())
        )

    def test_the_day_expression_compiles_for_postgresql_too(self) -> None:
        """The dialect-agnostic rule: this must work when the URL is repointed."""
        from sqlalchemy.dialects import postgresql

        from app.models import Order

        rendered = str(local_day(Order.processed_at, 330).compile(dialect=postgresql.dialect()))
        assert "INTERVAL '330 minutes'" in rendered


# ============================================================ M5
class TestOneLiveSyncPerWorkspace:
    """`start_sync` checked `runs.active()` then inserted, with nothing atomic
    between. Ten concurrent callers let two runs through, and both then fetched
    the same window and rebuilt the rollup."""

    @staticmethod
    def _run(workspace_id: int, status: str) -> SyncRun:
        return SyncRun(
            workspace_id=workspace_id,
            connection_id=1,
            trigger="manual",
            status=status,
            stage="queued" if status == "queued" else "orders",
            started_at=datetime.now(UTC),
        )

    def test_the_database_refuses_a_second_live_run(self, connected_ws: int) -> None:
        factory = session_module.get_session_factory()
        with factory() as db:
            db.add(self._run(connected_ws, "running"))
            db.commit()

        with pytest.raises(IntegrityError), factory() as db:
            db.add(self._run(connected_ws, "queued"))
            db.commit()

    def test_a_finished_run_does_not_block_the_next(self, connected_ws: int) -> None:
        """The index is partial: only queued and running rows are constrained."""
        factory = session_module.get_session_factory()
        with factory() as db:
            for _ in range(3):
                db.add(self._run(connected_ws, "finished"))
            db.commit()
            assert db.execute(text("SELECT COUNT(*) FROM sync_runs")).scalar() == 3

    def test_concurrent_starts_produce_exactly_one_run(
        self,
        connected: TestClient,
        shopify: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The reproduction, as a regression guard: ten callers, one run.

        The worker is stubbed out so the queued runs stay queued. Left to
        execute, each finishes in microseconds against the scripted store and the
        next caller legitimately starts a fresh one — which measures the fake
        store's speed rather than the guard.
        """
        monkeypatch.setattr("app.services.sync.runner.submit", lambda fn, name=None: None)

        codes: list[int] = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def start() -> None:
            barrier.wait()
            code = connected.post("/api/shopify/sync").status_code
            with lock:
                codes.append(code)

        threads = [threading.Thread(target=start) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        accepted = [c for c in codes if c == 202]
        assert len(accepted) == 1, f"expected one acceptance, got {codes}"
        assert all(c in (202, 409) for c in codes), f"unexpected status in {codes}"


# ============================================================ H1
class TestImportReplacementIsScopedByFormat:
    """The two file types destroyed each other.

    On the live workspace a stock sheet and a complaint export alternated for 24
    imports, each erasing the other, which is why ``sku_daily_complaints`` sat
    empty despite eleven successful complaint imports and no complaint figure
    anywhere answered the date range.
    """

    @staticmethod
    def upload(client: TestClient, body: str, name: str = "sheet.csv") -> dict:
        import io

        response = client.post(
            "/api/imports/upload",
            files={"file": (name, io.BytesIO(body.encode()), "text/csv")},
        )
        assert response.status_code == 200, response.text
        return response.json()

    STOCK = "SKU,Total Qty.,Total Orders.\nDD-1,10,3\nDD-2,20,4\nDD-3,30,5\n"
    COMPLAINTS = "Date,Order No,SKU Code,Reason\n2026-08-01,O-1,DD-1,Missing\n"

    def test_a_complaint_export_keeps_every_stock_sku(self, signed_in: TestClient) -> None:
        self.upload(signed_in, self.STOCK)
        self.upload(signed_in, self.COMPLAINTS, "complaints.csv")

        rows = signed_in.get("/api/analytics/skus?days=30&limit=50").json()["rows"]

        assert {r["sku"] for r in rows} == {"DD-1", "DD-2", "DD-3"}

    def test_a_complaint_export_keeps_the_stock_figures(self, signed_in: TestClient) -> None:
        """Only the complaint columns are its to state."""
        self.upload(signed_in, self.STOCK)
        self.upload(signed_in, self.COMPLAINTS, "complaints.csv")

        rows = {
            r["sku"]: r
            for r in signed_in.get("/api/analytics/skus?days=30&limit=50").json()["rows"]
        }

        assert rows["DD-1"]["total_qty"] == 10
        assert rows["DD-2"]["total_qty"] == 20
        assert rows["DD-1"]["total_orders"] == 3

    def test_a_stock_sheet_keeps_the_dated_complaints(self, signed_in: TestClient) -> None:
        """The other direction, and the one that was losing data."""
        self.upload(signed_in, self.COMPLAINTS, "complaints.csv")
        self.upload(signed_in, self.STOCK)

        scope = signed_in.get("/api/analytics/kpis?days=30").json()["complaint_scope"]

        assert scope["dated_skus"] == 1, "the stock sheet erased the complaint export"

    def test_alternating_the_two_never_loses_either(self, signed_in: TestClient) -> None:
        """The live workflow, four rounds of it."""
        for _ in range(4):
            self.upload(signed_in, self.STOCK)
            self.upload(signed_in, self.COMPLAINTS, "complaints.csv")

        body = signed_in.get("/api/analytics/kpis?days=30").json()
        rows = signed_in.get("/api/analytics/skus?days=30&limit=50").json()["rows"]

        assert {r["sku"] for r in rows} == {"DD-1", "DD-2", "DD-3"}, "stock survived"
        assert body["complaint_scope"]["dated_skus"] == 1, "dated complaints survived"
        assert body["total_quantity"] == 60, "10 + 20 + 30, unchanged by the complaint file"

    def test_a_stock_sheet_still_replaces_the_catalogue(self, signed_in: TestClient) -> None:
        """The behaviour that was right and must not regress: it states the whole
        catalogue, so a SKU it omits is gone."""
        self.upload(signed_in, self.STOCK)
        self.upload(signed_in, "SKU,Total Qty.\nDD-9,5\n")

        rows = signed_in.get("/api/analytics/skus?days=30&limit=50").json()["rows"]

        assert {r["sku"] for r in rows} == {"DD-9"}

    def test_a_complaint_export_still_replaces_the_dated_record(
        self, signed_in: TestClient
    ) -> None:
        """It owns that table, so the newest export is the whole of it."""
        self.upload(signed_in, self.COMPLAINTS, "complaints.csv")
        self.upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n2026-08-02,O-2,DD-7,Damage\n",
            "complaints.csv",
        )

        scope = signed_in.get("/api/analytics/kpis?days=365").json()["complaint_scope"]
        assert scope["dated_skus"] == 1

    def test_a_complaint_for_an_unknown_sku_still_appears(self, signed_in: TestClient) -> None:
        """A complaint against a product nobody imported is still a complaint."""
        self.upload(signed_in, self.STOCK)
        self.upload(
            signed_in,
            "Date,Order No,SKU Code,Reason\n2026-08-01,O-1,DD-NEW,Missing\n",
            "complaints.csv",
        )

        rows = {
            r["sku"] for r in signed_in.get("/api/analytics/skus?days=30&limit=50").json()["rows"]
        }
        assert "DD-NEW" in rows


# ============================================================ M2
class TestADateColumnIsNeverSilentlyIgnored:
    """`date` is a column the reader recognises, so it landed in
    `detected_columns` and the screen printed "matched Date → date" — which reads
    as confirmation the dates were used. Without a `reason` column they were not.
    """

    @staticmethod
    def upload(client: TestClient, body: str) -> dict:
        import io

        response = client.post(
            "/api/imports/upload",
            files={"file": ("sheet.csv", io.BytesIO(body.encode()), "text/csv")},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_a_date_without_a_reason_warns(self, signed_in: TestClient) -> None:
        body = self.upload(
            signed_in, "SKU,Date,Total Qty.\nDD-1,01-08-2026,10\nDD-2,02-08-2026,20\n"
        )

        assert "date_column_ignored" in body["warnings"]

    def test_the_warning_is_sharper_when_rows_were_merged(self, signed_in: TestClient) -> None:
        """One row per SKU per day is the dangerous shape: the rows are merged as
        duplicates and their quantities summed across days."""
        body = self.upload(
            signed_in, "SKU,Date,Total Qty.\nDD-1,01-08-2026,10\nDD-1,02-08-2026,10\n"
        )

        assert "date_column_ignored_with_duplicates" in body["warnings"]
        # The summing still happens — the file is ambiguous and this is the
        # documented reading. What changed is that it is no longer silent.
        assert body["duplicates"][0]["merged_quantity"] == 20

    def test_a_real_complaint_export_does_not_warn(self, signed_in: TestClient) -> None:
        """It has a Reason column, so its dates are used and there is nothing to say."""
        body = self.upload(signed_in, "SKU Code,Date,Reason\nDD-1,01-08-2026,Missing\n")

        assert body["warnings"] == []
        assert body["sheet_format"] == "complaints"

    def test_a_sheet_with_no_date_column_does_not_warn(self, signed_in: TestClient) -> None:
        body = self.upload(signed_in, "SKU,Total Qty.\nDD-1,10\n")

        assert body["warnings"] == []


# ============================================================ L1
class TestTheSkuColumnSortsTheSameOnEveryScreen:
    """SKU Performance sorted on `sku_normalized`, which drops separators, so it
    put `0381b_velvet_heating_bag` before `0381_velvet_heating_bag` while the
    Dashboard — sorting on the SKU as displayed — put them the other way round."""

    @pytest.fixture
    def stocked(self, signed_in: TestClient) -> TestClient:
        import io

        body = "SKU,Total Qty.\n" + "".join(
            f"{sku},5\n" for sku in ("0381_velvet", "0381b_velvet", "AB_c", "ABc")
        )
        assert (
            signed_in.post(
                "/api/imports/upload",
                files={"file": ("s.csv", io.BytesIO(body.encode()), "text/csv")},
            ).status_code
            == 200
        )
        return signed_in

    def test_ascending_sku_order_is_the_displayed_order(self, stocked: TestClient) -> None:
        rows = stocked.get(
            "/api/analytics/performance?days=30&sort=sku&descending=false&limit=50"
        ).json()["rows"]
        skus = [r["sku"] for r in rows]

        assert skus == sorted(skus, key=str.lower), skus

    def test_the_underscore_orders_before_a_letter(self, stocked: TestClient) -> None:
        """`_` is 0x5F and `b` is 0x62, so the displayed order puts `0381_` first.
        On the normalised key the separator is gone and `b` wins instead."""
        rows = stocked.get(
            "/api/analytics/performance?days=30&sort=sku&descending=false&limit=50"
        ).json()["rows"]
        skus = [r["sku"] for r in rows]

        assert skus.index("0381_velvet") < skus.index("0381b_velvet")


# ============================================================ L2
class TestRetryRefusesWhenNothingFailed:
    def test_a_successful_run_has_nothing_to_retry(
        self,
        connected: TestClient,
        shopify: object,
    ) -> None:
        """It used to start a full sync: 3,329 orders and a rollup rebuild for a
        run that had already succeeded."""
        assert connected.post("/api/shopify/sync").status_code == 202
        # The worker runs inline under test, so the run is finished by now.
        state = connected.get("/api/shopify/sync").json()
        assert state["run"]["result"] == "success"

        response = connected.post("/api/shopify/sync/retry")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "nothing_to_retry"

    def test_a_failed_run_can_still_be_retried(
        self,
        connected: TestClient,
        shopify: object,
    ) -> None:
        """The control must keep working for the case it exists for."""
        from app.models import SyncRun as Run

        factory = session_module.get_session_factory()
        with factory() as db:
            db.add(
                Run(
                    workspace_id=1,
                    connection_id=1,
                    trigger="manual",
                    status="finished",
                    stage="done",
                    result="partial",
                    error_code="rollup_failed",
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                )
            )
            db.commit()

        assert connected.post("/api/shopify/sync/retry").status_code == 202
