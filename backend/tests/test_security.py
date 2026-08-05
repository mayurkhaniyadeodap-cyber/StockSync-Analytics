"""Password hashing and token handling.

This is a security boundary, so the tests target the ways it can be *broken*,
not just the happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import Settings
from app.core import security


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", jwt_secret="a" * 48, database_url="sqlite+pysqlite:///:memory:")


class TestPasswords:
    def test_hash_then_verify(self) -> None:
        stored = security.hash_password("correct horse battery staple")

        assert security.verify_password(stored, "correct horse battery staple")

    def test_wrong_password_is_rejected(self) -> None:
        stored = security.hash_password("correct horse battery staple")

        assert not security.verify_password(stored, "Correct horse battery staple")

    def test_hash_is_not_the_password(self) -> None:
        stored = security.hash_password("hunter2hunter2")

        assert "hunter2" not in stored
        assert stored.startswith("$argon2id$")

    def test_same_password_hashes_differently(self) -> None:
        """A per-hash salt: identical passwords must not collide in the table."""
        a = security.hash_password("same-password-twice")
        b = security.hash_password("same-password-twice")

        assert a != b

    def test_garbage_hash_does_not_raise(self) -> None:
        assert not security.verify_password("not-a-hash", "anything")

    def test_dummy_verify_exercises_the_real_path(self) -> None:
        """Guards the timing-equalisation used to prevent user enumeration."""
        security.dummy_verify()  # must not raise


class TestAccessTokens:
    def test_round_trip(self, settings: Settings) -> None:
        token, expires = security.create_access_token(
            settings, user_id=7, workspace_id=3, session_id=11
        )

        claims = security.decode_access_token(settings, token)

        assert claims.user_id == 7
        assert claims.workspace_id == 3
        assert claims.session_id == 11
        assert claims.expires_at == expires.replace(microsecond=0)

    def test_expired_token_is_rejected(self, settings: Settings) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        token, _ = security.create_access_token(
            settings, user_id=1, workspace_id=1, session_id=1, now=past
        )

        with pytest.raises(security.TokenError, match="expired"):
            security.decode_access_token(settings, token)

    def test_tampered_payload_is_rejected(self, settings: Settings) -> None:
        token, _ = security.create_access_token(settings, user_id=1, workspace_id=1, session_id=1)
        header, payload, _signature = token.split(".")
        forged = f"{header}.{payload}.{'A' * 43}"

        with pytest.raises(security.TokenError):
            security.decode_access_token(settings, forged)

    def test_token_signed_with_another_key_is_rejected(self, settings: Settings) -> None:
        other = Settings(
            env="test", jwt_secret="b" * 48, database_url="sqlite+pysqlite:///:memory:"
        )
        token, _ = security.create_access_token(other, user_id=1, workspace_id=1, session_id=1)

        with pytest.raises(security.TokenError):
            security.decode_access_token(settings, token)

    def test_alg_none_token_is_rejected(self, settings: Settings) -> None:
        """The classic JWT attack: an unsigned token claiming alg=none."""
        forged = jwt.encode(
            {
                "sub": "1",
                "wsp": 1,
                "sid": 1,
                "typ": "access",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(security.TokenError):
            security.decode_access_token(settings, forged)

    def test_token_of_the_wrong_type_is_rejected(self, settings: Settings) -> None:
        """A token minted for another purpose must not work as an access token."""
        other_purpose = jwt.encode(
            {
                "sub": "1",
                "wsp": 1,
                "sid": 1,
                "typ": "password_reset",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            settings.resolved_jwt_secret,
            algorithm="HS256",
        )

        with pytest.raises(security.TokenError, match="wrong token type"):
            security.decode_access_token(settings, other_purpose)

    def test_garbage_is_rejected(self, settings: Settings) -> None:
        with pytest.raises(security.TokenError):
            security.decode_access_token(settings, "not.a.token")


class TestRefreshTokens:
    def test_raw_token_is_not_stored(self) -> None:
        raw, digest = security.new_refresh_token()

        assert raw != digest
        assert len(digest) == 64
        assert security.hash_refresh_token(raw) == digest

    def test_tokens_are_unique(self) -> None:
        tokens = {security.new_refresh_token()[0] for _ in range(50)}

        assert len(tokens) == 50

    def test_remember_me_changes_lifetime(self, settings: Settings) -> None:
        long = security.refresh_token_lifetime(settings, remember_me=True)
        short = security.refresh_token_lifetime(settings, remember_me=False)

        assert long > short
        assert long == timedelta(days=settings.refresh_token_days)


class TestSecretHandling:
    def test_production_requires_a_real_secret(self) -> None:
        with pytest.raises(ValueError, match="STOCKSYNC_JWT_SECRET"):
            Settings(env="production", jwt_secret="", _env_file=None)  # type: ignore[call-arg]

    def test_production_rejects_a_short_secret(self) -> None:
        with pytest.raises(ValueError, match="at least 32"):
            Settings(env="production", jwt_secret="short", _env_file=None)  # type: ignore[call-arg]

    def test_development_falls_back_to_an_ephemeral_secret(self) -> None:
        settings = Settings(env="development", jwt_secret="", _env_file=None)  # type: ignore[call-arg]

        assert len(settings.resolved_jwt_secret) >= 32
