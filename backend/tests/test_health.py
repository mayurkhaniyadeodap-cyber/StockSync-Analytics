"""Health endpoint contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app


def test_health_reports_ok_when_database_reachable(client: TestClient, healthy_db: None) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"
    assert body["database"]["status"] == "ok"
    assert body["database"]["latency_ms"] == pytest.approx(1.23)


def test_health_reports_503_when_database_unreachable(
    client: TestClient, unreachable_db: None
) -> None:
    """A degraded database must fail the check.

    This is the assertion that stops a load balancer or a deploy gate from
    treating a database outage as healthy.
    """
    response = client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "unreachable"
    assert body["database"]["reason"] == "OperationalError"


def test_unknown_route_uses_the_error_envelope(client: TestClient, healthy_db: None) -> None:
    """Design doc §16: every error says what happened and what to do next."""
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert error["next"]


@pytest.mark.integration
def test_health_reports_ok_against_a_real_database(tmp_path, monkeypatch) -> None:
    """The success path, end to end, with no stubs.

    SQLite makes this cheap enough to run on every commit, so /api/health's
    happy path is verified rather than assumed.
    """
    from app import config as config_module
    from app.db import session as session_module

    monkeypatch.setenv(
        "STOCKSYNC_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'h.db').as_posix()}"
    )
    for cached in (
        config_module.get_settings,
        session_module.get_engine,
        session_module.get_session_factory,
    ):
        cached.cache_clear()

    try:
        with TestClient(create_app(config_module.get_settings())) as real_client:
            response = real_client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"]["status"] == "ok"
        assert body["database"]["reason"] is None
        assert body["database"]["latency_ms"] is not None
    finally:
        session_module.get_engine().dispose()
        for cached in (
            config_module.get_settings,
            session_module.get_engine,
            session_module.get_session_factory,
        ):
            cached.cache_clear()
