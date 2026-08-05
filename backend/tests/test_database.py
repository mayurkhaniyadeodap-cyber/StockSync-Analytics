"""Database wiring.

These run against a real SQLite file rather than a stub. The pragma assertions
are the important ones: SQLite silently ignores foreign keys unless asked, so
without this test a schema full of FK constraints could enforce nothing and
every test would still pass.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from app import config as config_module
from app import db as db_module  # noqa: F401  (namespace import keeps mypy happy)
from app.config import REPO_ROOT, Settings, get_settings
from app.db import session as session_module
from app.models import Order, OrderLineItem


@pytest.fixture
def sqlite_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the cached engine at a throwaway SQLite file."""
    db_path = tmp_path / "nested" / "stocksync.db"
    monkeypatch.setenv("STOCKSYNC_DATABASE_URL", f"sqlite+pysqlite:///{db_path.as_posix()}")

    for cached in (
        config_module.get_settings,
        session_module.get_engine,
        session_module.get_session_factory,
    ):
        cached.cache_clear()

    yield db_path

    session_module.get_engine().dispose()
    for cached in (
        config_module.get_settings,
        session_module.get_engine,
        session_module.get_session_factory,
    ):
        cached.cache_clear()


@pytest.mark.integration
def test_ping_succeeds_against_a_real_file(sqlite_env: Path) -> None:
    status = session_module.ping_database()

    assert status.ok
    assert status.reason is None
    assert status.latency_ms is not None


@pytest.mark.integration
def test_engine_creates_the_parent_directory(sqlite_env: Path) -> None:
    """A file-backed SQLite database cannot create its own directory."""
    assert not sqlite_env.parent.exists()

    session_module.ping_database()

    assert sqlite_env.parent.is_dir()
    assert sqlite_env.exists()


@pytest.mark.integration
def test_foreign_keys_are_enforced(sqlite_env: Path) -> None:
    """SQLite defaults foreign_keys to OFF, which makes every FK decorative."""
    engine = session_module.get_engine()

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


@pytest.mark.integration
def test_foreign_key_violation_actually_raises(sqlite_env: Path) -> None:
    """The pragma is only meaningful if a bad write is rejected."""
    from sqlalchemy.exc import IntegrityError

    engine = session_module.get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER NOT NULL REFERENCES parent(id)"
                ")"
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 999)"))


@pytest.mark.integration
def test_wal_mode_is_enabled(sqlite_env: Path) -> None:
    """WAL lets the dashboard read while a Shopify sync writes."""
    engine = session_module.get_engine()

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"


@pytest.mark.integration
def test_busy_timeout_is_applied(sqlite_env: Path) -> None:
    """The connection honours the *configured* timeout, whatever it is.

    This asserted a literal 10,000 ms, which stopped being the default when the
    timeout was raised to 30 s to cover the rollup swap. It kept passing on a
    developer machine only because the local `.env` still said 10 — and failed
    the moment it ran anywhere without one, which is every clone and CI. Reading
    the setting is what makes it test the wiring rather than a number.
    """
    settings = get_settings()
    engine = session_module.get_engine()

    with engine.connect() as connection:
        applied = connection.execute(text("PRAGMA busy_timeout")).scalar()

    assert applied == int(settings.db_busy_timeout_seconds * 1000)


class TestSettings:
    def test_sqlite_is_the_default(self) -> None:
        get_settings.cache_clear()
        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.is_sqlite
        assert settings.dialect == "sqlite"

    def test_dialect_is_read_from_the_url(self) -> None:
        settings = Settings(database_url="postgresql+psycopg://u:p@h:5432/d")

        assert settings.dialect == "postgresql"
        assert not settings.is_sqlite

    def test_bare_postgresql_url_is_rejected(self) -> None:
        """It would silently select psycopg2, which is not installed."""
        with pytest.raises(ValueError, match="postgresql\\+psycopg"):
            Settings(database_url="postgresql://user:pass@localhost/db")

    def test_in_memory_url_has_no_path_to_create(self) -> None:
        settings = Settings(database_url="sqlite+pysqlite:///:memory:")

        assert settings.sqlite_path() is None

    def test_relative_sqlite_path_is_anchored_to_the_repo_root(self) -> None:
        """Not to the working directory.

        Otherwise `alembic upgrade head` from backend/ and a server started from
        the repo root would use two different files, and the schema would appear
        applied from one directory and missing from the other.
        """
        settings = Settings(database_url="sqlite+pysqlite:///./data/stocksync.db")

        resolved = settings.sqlite_path()
        assert resolved is not None
        assert resolved.is_absolute()
        assert resolved == (REPO_ROOT / "data" / "stocksync.db").resolve()

    def test_absolute_sqlite_path_is_left_alone(self, tmp_path: Path) -> None:
        target = (tmp_path / "explicit.db").resolve()
        settings = Settings(database_url=f"sqlite+pysqlite:///{target.as_posix()}")

        assert settings.sqlite_path() == target

    def test_memory_url_is_not_rewritten(self) -> None:
        settings = Settings(database_url="sqlite+pysqlite:///:memory:")

        assert settings.database_url == "sqlite+pysqlite:///:memory:"

    def test_dotenv_style_cors_origins_parse(self) -> None:
        """A bare comma-separated string, as a .env file supplies it.

        Without NoDecode on the field, pydantic-settings JSON-decodes it first
        and the application refuses to start.
        """
        settings = Settings(cors_origins="http://localhost:5173,http://127.0.0.1:5173")  # type: ignore[arg-type]

        assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]

    def test_non_sqlite_has_no_sqlite_path(self) -> None:
        settings = Settings(database_url="postgresql+psycopg://u:p@h:5432/d")

        assert settings.sqlite_path() is None

    def test_sqlite_engine_gets_no_pool_arguments(self) -> None:
        """QueuePool arguments are invalid for SQLite's default pool."""
        settings = Settings(database_url="sqlite+pysqlite:///:memory:")

        kwargs = session_module._engine_kwargs(settings)

        assert "pool_size" not in kwargs
        assert "max_overflow" not in kwargs
        assert kwargs["connect_args"]["check_same_thread"] is False

    def test_server_dialect_keeps_pool_arguments(self) -> None:
        settings = Settings(database_url="postgresql+psycopg://u:p@h:5432/d")

        kwargs = session_module._engine_kwargs(settings)

        assert kwargs["pool_size"] == 5
        assert kwargs["pool_pre_ping"] is True


class TestOneDefinitionOfASale:
    """ "Counts as a sale" is a business rule, and there is one of it.

    There used to be two: ``Order.counts_as_sale`` checked cancellation and
    financial status, while the rollup also required a non-blank SKU and a
    ``processed_at``. The model would have called 58 blank-SKU units sales that
    the rollup excluded. The property is gone; both callers now read the same
    four conditions.
    """

    def test_the_sql_and_the_row_predicate_agree(self) -> None:
        from app.models import counts_as_sale, sale_filters

        # Four conditions in the SQL…
        assert len(sale_filters()) == 4

        # …and the same four for a loaded row.
        now = datetime.now(UTC)

        def order(**over: object) -> Order:
            base = {
                "cancelled_at": None,
                "financial_status": "paid",
                "processed_at": now,
            }
            base.update(over)
            return Order(**base)  # type: ignore[arg-type]

        def line(sku: str = "dd1001") -> OrderLineItem:
            return OrderLineItem(sku_normalized=sku)

        assert counts_as_sale(order(), line()) is True
        assert counts_as_sale(order(cancelled_at=now), line()) is False
        assert counts_as_sale(order(financial_status="refunded"), line()) is False
        assert counts_as_sale(order(financial_status="voided"), line()) is False
        assert counts_as_sale(order(processed_at=None), line()) is False
        # The two the old model property did not check.
        assert counts_as_sale(order(), line(sku="")) is False

    def test_a_null_financial_status_is_a_sale(self) -> None:
        """Shopify can leave it unset; that is not a refund."""
        from app.models import counts_as_sale

        assert (
            counts_as_sale(
                Order(cancelled_at=None, financial_status=None, processed_at=datetime.now(UTC)),
                OrderLineItem(sku_normalized="dd1"),
            )
            is True
        )

    def test_the_model_no_longer_carries_its_own_copy(self) -> None:
        """A second definition is how the two came to disagree."""
        assert not hasattr(Order, "counts_as_sale")

    def test_the_rollup_reads_the_shared_rule(self) -> None:
        """Not a copy of it — the same object."""
        import inspect

        from app.services import metrics

        assert "sale_filters()" in inspect.getsource(metrics.refresh)
