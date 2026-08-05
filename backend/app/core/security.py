"""Password hashing and token handling.

Two separate credentials, deliberately:

* a short-lived **access token** (stateless JWT, 15 minutes) that authorises
  requests, and
* a long-lived **refresh token** (opaque random string, stored hashed) that
  mints new access tokens and can be revoked.

A JWT alone cannot be invalidated before it expires, so "Log out" would be a
lie. The refresh row is what makes logout real.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings

# argon2-cffi's defaults track the OWASP recommendation and are re-tuned by the
# library over time. Pinning our own numbers here would freeze that.
_hasher = PasswordHasher()

# Not a credential: the value of the "typ" claim, used to stop a token minted
# for another purpose being replayed as an access token.
ACCESS_TOKEN_TYPE = "access"  # noqa: S105


class TokenError(Exception):
    """Raised when a token is absent, malformed, expired, or not ours."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: int
    workspace_id: int
    session_id: int
    expires_at: datetime


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash with argon2id. The salt is generated per call and embedded."""
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """Constant-time verification. Returns False rather than raising."""
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """True when the hash predates a parameter change and should be upgraded.

    Called after a successful login, where the plaintext is briefly available.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Burn roughly one real verification's worth of time.

    Called when the email does not exist, so a missing account and a wrong
    password take the same time and the login endpoint cannot be used to
    enumerate users.

    The reference hash is computed lazily from a random password and cached, so
    this exercises the genuine argon2 code path. A hand-written constant would
    raise InvalidHashError immediately and defeat the purpose.
    """
    verify_password(_dummy_hash(), "not-the-password")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


# --------------------------------------------------------------------------
# access tokens
# --------------------------------------------------------------------------


def create_access_token(
    settings: Settings,
    *,
    user_id: int,
    workspace_id: int,
    session_id: int,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Sign a short-lived access token. Returns the token and its expiry."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.access_token_minutes)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "wsp": workspace_id,
        "sid": session_id,
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.resolved_jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    """Verify and decode, or raise TokenError.

    ``algorithms`` is an allow-list of exactly one entry, so a token whose header
    claims ``none`` or a different algorithm is rejected rather than trusted —
    the JWT algorithm-confusion attack.
    """
    try:
        payload = jwt.decode(
            token,
            settings.resolved_jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid") from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        # Stops a token minted for another purpose being replayed as an access
        # token.
        raise TokenError("wrong token type")

    try:
        return AccessTokenClaims(
            user_id=int(payload["sub"]),
            workspace_id=int(payload["wsp"]),
            session_id=int(payload["sid"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("malformed claims") from exc


# --------------------------------------------------------------------------
# refresh tokens
# --------------------------------------------------------------------------


def new_refresh_token() -> tuple[str, str]:
    """Generate an opaque refresh token. Returns ``(raw, sha256_hex)``.

    Only the digest is stored. The raw value goes to the client once, in an
    httpOnly cookie, and is never recoverable from the database.

    Plain SHA-256 rather than argon2: this is 256 bits of CSPRNG output, not a
    human-chosen password, so there is nothing for a slow hash to defend
    against — and refresh happens on a hot path.
    """
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_token_lifetime(settings: Settings, *, remember_me: bool) -> timedelta:
    """How long a refresh token lives.

    "Keep me signed in" (design doc §6) chooses between a long-lived token and
    one that lasts a working day.
    """
    if remember_me:
        return timedelta(days=settings.refresh_token_days)
    return timedelta(hours=settings.refresh_token_session_hours)
