"""Shared fixtures.

Most tests stub ``ping_database`` — the single seam the health endpoint uses —
so they stay fast and hermetic. Tests marked ``integration`` run against a real
SQLite file in a tmp_path instead; with SQLite that costs nothing and needs no
service, so the success path is genuinely exercised rather than mocked.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.config import get_settings as get_settings_cached
from app.core.security import hash_password
from app.db import session as session_module
from app.db.base import Base
from app.db.session import DatabaseStatus
from app.main import create_app
from app.models import User, UserPreferences, Workspace

# Deliberately literal: the values are throwaway and never leave the process.
TEST_PASSWORD = "a-perfectly-fine-password"
TEST_EMAIL = "Admin@Deodap.in"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="test",
        debug=True,
        database_url="sqlite+pysqlite:///:memory:",
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Test client.

    The startup probe is stubbed as well as the route's, so entering the lifespan
    doesn't open a real connection (and doesn't wait on a timeout when the test
    database is absent).
    """
    monkeypatch.setattr("app.main.ping_database", lambda: DatabaseStatus(ok=True, latency_ms=1.0))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def healthy_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.health.ping_database",
        lambda: DatabaseStatus(ok=True, latency_ms=1.23),
    )


@pytest.fixture
def unreachable_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.health.ping_database",
        lambda: DatabaseStatus(ok=False, reason="OperationalError"),
    )


@pytest.fixture(autouse=True)
def _no_ambient_shopify_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own .env out of every test.

    Settings reads ../.env, so a real SHOPIFY_STORE_URL / SHOPIFY_ADMIN_API_TOKEN
    would silently connect a store in tests that assert on the empty state, and
    would make assertions about defaults depend on whose machine is running.
    Autouse rather than folded into a client fixture because any test may
    construct Settings directly.
    """
    for name in ("SHOPIFY_STORE_URL", "SHOPIFY_ADMIN_API_TOKEN"):
        monkeypatch.setenv(name, "")


def _clear_caches() -> None:
    for cached in (
        get_settings_cached,
        session_module.get_engine,
        session_module.get_session_factory,
    ):
        cached.cache_clear()


@pytest.fixture
def api(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client backed by a real, throwaway SQLite file with one seeded user.

    A file rather than ``:memory:``: an in-memory database is per-connection,
    so the request's session and the test's session would see different
    databases and every write would appear to vanish.
    """
    monkeypatch.setenv(
        "STOCKSYNC_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'api.db').as_posix()}"
    )
    monkeypatch.setenv("STOCKSYNC_ENV", "test")
    monkeypatch.setenv("STOCKSYNC_JWT_SECRET", "t" * 48)
    monkeypatch.setenv(
        "STOCKSYNC_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    )
    # The ambient Shopify credential is already blanked by the autouse fixture
    # above; env_shopify opts back in.

    _clear_caches()
    engine = session_module.get_engine()
    Base.metadata.create_all(engine)

    with session_module.get_session_factory()() as db:
        workspace = Workspace(name="Deodap Retail", slug="deodap")
        db.add(workspace)
        db.flush()
        user = User(
            workspace_id=workspace.id,
            email=TEST_EMAIL,
            email_normalized=TEST_EMAIL.lower(),
            password_hash=hash_password(TEST_PASSWORD),
            full_name="Administrator",
            role="Admin",
        )
        db.add(user)
        db.flush()
        db.add(UserPreferences(user_id=user.id))
        db.commit()

    with TestClient(create_app(get_settings_cached())) as client:
        yield client

    engine.dispose()
    _clear_caches()


@pytest.fixture
def env_shopify(signed_in: TestClient) -> TestClient:
    """``signed_in``, with a Shopify credential configured in the environment.

    Patched onto the live Settings object rather than through env vars: the app
    is already built and holds its own instance, so setenv at this point would
    be read by nothing.
    """
    settings = signed_in.app.state.settings
    settings.shopify_store_url = "envstore.myshopify.com"
    settings.shopify_admin_api_token = "shpat_from_the_environment_file"
    return signed_in


@pytest.fixture
def signed_in(api: TestClient) -> TestClient:
    """``api``, already authenticated. Every M2 endpoint requires a session."""
    response = api.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD, "remember_me": True},
    )
    if response.status_code != 200:  # pragma: no cover - fixture wiring failure
        raise RuntimeError(f"fixture login failed: {response.status_code} {response.text}")
    return api
