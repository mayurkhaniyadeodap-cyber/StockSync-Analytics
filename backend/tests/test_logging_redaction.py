"""Credential redaction.

The brief's rule is *never log tokens*. These tests are the enforcement — if
someone widens a log line later, this is what catches it.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.logging import redact


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("token is shpat_YOURSHOPIFYACCESSTOKENPLACEHOLDER", "YOURSHOPIFYACCESSTOKENPLACEHOLDER"),
        ("secret shpss_YOURSHOPIFYAPPSECRETPLACEHOLDER", "YOURSHOPIFYAPPSECRETPLACEHOLDER"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc", "eyJhbGciOiJIUzI1NiJ9.abc"),
        ('{"access_token": "shpat_abc123"}', "shpat_abc123"),
        ("password=hunter2 user=admin", "hunter2"),
        ("client_secret: GOCSPX-1a2b3c", "GOCSPX-1a2b3c"),
        ("postgresql+psycopg://strata:s3cr3t@localhost:5432/strata", "s3cr3t"),
    ],
)
def test_redact_removes_credentials(raw: str, must_not_contain: str) -> None:
    scrubbed = redact(raw)

    assert must_not_contain not in scrubbed
    assert "***" in scrubbed


def test_redact_leaves_ordinary_text_alone() -> None:
    message = "imported 1,238 rows from Master Inventory FY26 in 812 ms"

    assert redact(message) == message


def test_safe_database_url_masks_the_password() -> None:
    settings = Settings(database_url="postgresql+psycopg://strata:s3cr3t@db.internal:5432/strata")

    safe = settings.safe_database_url()

    assert "s3cr3t" not in safe
    assert safe == "postgresql+psycopg://strata:***@db.internal:5432/strata"
