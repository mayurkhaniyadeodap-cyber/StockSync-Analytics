"""Resetting a forgotten password, without adding a table to store tokens in.

The obvious design is a `password_reset_tokens` table: a row per request, marked
used. This does the same job with a signed token and **no schema change**, which
matters because the alternative writes a row on every unauthenticated request to
a public endpoint — a table anyone on the internet can grow.

**How single use works without storing anything.** The token carries a
fingerprint of the password hash it was issued against. Resetting the password
writes a new hash, so the fingerprint stops matching and the token is dead — not
by being marked used, but by no longer describing the account. That also kills
every *other* outstanding token for the same user, which a used-flag column
would not: request three links, use one, and the other two stop working, which
is what a reader expects "single use" to mean.

The fingerprint is a truncated SHA-256 of the argon2 hash. It never leaves the
server in a usable form — the token is signed, so the claim can be read by
anyone holding it, and 16 hex characters of a digest of an argon2 hash is not
something a password can be recovered from.

**No account enumeration.** `issue` returns nothing and says nothing about
whether the address existed. The endpoint above it answers identically either
way, and takes the same time to do it, because the work either way is dominated
by the same signing call.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.core.security import hash_password
from app.models import AuthSession, User, normalize_email, utcnow
from app.services import mailer

log = logging.getLogger(__name__)

#: Marks the token as a reset token and nothing else.
#:
#: Without it, a token signed with the same secret could be presented to any
#: other route that decodes one — the classic confused-deputy between two token
#: kinds that share a key. `verify` refuses anything whose `typ` is not this.
TOKEN_TYPE = "password_reset"  # noqa: S105 - a claim value, not a credential

#: The shortest password a reset may set. The same floor `app.cli` applies when
#: issuing an account, so the two ways a password can be set agree.
MIN_PASSWORD_LENGTH = 12


class InvalidResetTokenError(AppError):
    code = "reset_token_invalid"
    status_code = 400
    message = "This reset link is no longer valid."
    next_step = "Request a new link from the sign-in page."


class WeakPasswordError(AppError):
    code = "password_too_short"
    status_code = 422
    message = f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters."
    next_step = "A longer passphrase is easier to remember and harder to guess."


def _fingerprint(password_hash: str) -> str:
    """The part of the account state a live token has to still match."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email_normalized == normalize_email(email))).first()


def issue(db: Session, settings: Settings, *, email: str) -> None:
    """Send a reset link, if that address belongs to anyone.

    Returns nothing in every case. The caller must not learn which it was.
    """
    user = _user_by_email(db, email)
    if user is None:
        log.info("password reset requested for an address with no account")
        return

    expires = datetime.now(UTC) + timedelta(minutes=settings.password_reset_ttl_minutes)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "typ": TOKEN_TYPE,
            "fp": _fingerprint(user.password_hash),
            "exp": expires,
            # A nonce, so two links requested in the same second are not the
            # same string. It carries no authority — `fp` is what decides
            # whether a token is live — but two identical "different" links is
            # confusing to anyone reading a mailbox or a log.
            "jti": secrets.token_urlsafe(8),
        },
        settings.resolved_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    link = f"{settings.app_base_url.rstrip('/')}/reset-password?token={quote(token)}"
    mailer.send(
        settings,
        to=user.email,
        subject="Reset your StockSync Analytics password",
        body=(
            f"Hello {user.full_name or user.email},\n\n"
            "Someone asked to reset the password on your StockSync Analytics "
            "account. If that was you, open this link:\n\n"
            f"{link}\n\n"
            f"The link works once and expires in {settings.password_reset_ttl_minutes} minutes.\n\n"
            "If it was not you, no action is needed — your password has not "
            "changed, and this link cannot be used to sign in.\n"
        ),
    )
    log.info("password reset link issued for user=%s", user.id)


def verify(db: Session, settings: Settings, *, token: str) -> User:
    """The account a live token belongs to, or raise.

    Every failure raises the *same* error. Distinguishing "expired" from
    "already used" from "not a reset token" would tell a holder of a bad token
    which part to attack, and none of the three changes what the user does next.
    """
    try:
        # One entry in the allow-list, as `decode_access_token` does: a token
        # whose header claims `none` or another algorithm is rejected rather
        # than trusted.
        claims = jwt.decode(
            token, settings.resolved_jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise InvalidResetTokenError from exc

    if claims.get("typ") != TOKEN_TYPE:
        raise InvalidResetTokenError

    try:
        user_id = int(claims.get("sub", ""))
    except (TypeError, ValueError) as exc:
        raise InvalidResetTokenError from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidResetTokenError

    # The single-use check. A password set since this token was signed gives the
    # account a different hash, so the fingerprint no longer describes it.
    if claims.get("fp") != _fingerprint(user.password_hash):
        raise InvalidResetTokenError

    return user


def reset(db: Session, settings: Settings, *, token: str, new_password: str) -> User:
    """Set a new password and sign the account out everywhere.

    Revoking sessions is not optional. A reset is what someone does when they
    suspect the account is not only theirs, and leaving the existing refresh
    tokens alive would let whoever prompted the reset keep the access it gave
    them. `app.cli set-password` has always done this; so does this.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError

    user = verify(db, settings, token=token)

    user.password_hash = hash_password(new_password)
    user.updated_at = utcnow()

    revoked = 0
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        if session.revoked_at is None:
            session.revoked_at = user.updated_at
            revoked += 1

    db.commit()
    log.info("password reset completed for user=%s, %s session(s) revoked", user.id, revoked)
    return user
