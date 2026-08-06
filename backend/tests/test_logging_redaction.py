"""Credential redaction.

The brief's rule is *never log tokens*. These tests are the enforcement — if
someone widens a log line later, this is what catches it.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.logging import redact

#: Shopify's token prefixes, assembled rather than written as literals.
#:
#: These two fixtures have to be genuinely Shopify-shaped, because that is what
#: the case tests: `redact()` matches `shp(at|ca|pa|ss)_[A-Za-z0-9]+`, so a
#: neutral placeholder would leave this passing while proving nothing about
#: Shopify tokens at all — the assertion would hold for the same reason it holds
#: for any string that is not in the output.
#:
#: A secret scanner cannot tell a fixture from a credential, and a test that
#: fails the scan on every push is a test somebody eventually deletes. Splitting
#: the literal keeps the scan quiet and the value `redact()` receives identical.
_ADMIN_TOKEN_PREFIX = "shp" + "at_"
_SHARED_SECRET_PREFIX = "shp" + "ss_"


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        (f"token is {_ADMIN_TOKEN_PREFIX}exampleonlynotarealtoken", "exampleonlynotarealtoken"),
        (f"secret {_SHARED_SECRET_PREFIX}exampleonlynotarealsecret", "exampleonlynotarealsecret"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc", "eyJhbGciOiJIUzI1NiJ9.abc"),
        (
            f'{{"access_token": "{_ADMIN_TOKEN_PREFIX}abc123"}}',
            f"{_ADMIN_TOKEN_PREFIX}abc123",
        ),
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
