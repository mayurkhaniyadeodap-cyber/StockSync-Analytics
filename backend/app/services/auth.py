"""Authentication service: the rules, kept out of the route handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.core import security
from app.core.errors import AppError
from app.models import AuthSession, User, UserPreferences, normalize_email

log = logging.getLogger(__name__)


class InvalidCredentialsError(AppError):
    """Wrong email, wrong password, or a deactivated account.

    Deliberately one error for all three. Design doc §6 specifies a single
    banner reading "Incorrect email or password." — telling the user *which*
    part was wrong hands an attacker a user-enumeration oracle.
    """

    code = "invalid_credentials"
    status_code = 401
    message = "Incorrect email or password."
    next_step = "Check both fields and try again."


class SessionExpiredError(AppError):
    code = "session_expired"
    status_code = 401
    message = "Your session has ended."
    next_step = "Sign in again to continue."


@dataclass(frozen=True)
class IssuedSession:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    user: User


def _load_user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(
        select(User)
        .where(User.email_normalized == normalize_email(email))
        .options(selectinload(User.workspace), selectinload(User.preferences))
    ).first()


def authenticate(db: Session, *, email: str, password: str) -> User:
    """Verify credentials, or raise InvalidCredentialsError."""
    user = _load_user_by_email(db, email)

    if user is None:
        # Spend the same time as a real verification so response latency does
        # not reveal whether the address exists.
        security.dummy_verify()
        raise InvalidCredentialsError

    if not security.verify_password(user.password_hash, password):
        raise InvalidCredentialsError

    if not user.is_active:
        raise InvalidCredentialsError

    # The plaintext is only available here, so this is the one place a hash can
    # be upgraded after an argon2 parameter change.
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    return user


def ensure_preferences(db: Session, user: User) -> UserPreferences:
    """Every user has a preferences row; create it lazily on first need."""
    if user.preferences is not None:
        return user.preferences
    preferences = UserPreferences(user_id=user.id)
    db.add(preferences)
    db.flush()
    user.preferences = preferences
    return preferences


def issue_session(
    db: Session,
    settings: Settings,
    *,
    user: User,
    remember_me: bool,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    """Create a refresh session and a matching access token."""
    now = datetime.now(UTC)
    raw_refresh, refresh_hash = security.new_refresh_token()
    lifetime = security.refresh_token_lifetime(settings, remember_me=remember_me)

    auth_session = AuthSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        issued_at=now,
        expires_at=now + lifetime,
        user_agent=(user_agent or "")[:255] or None,
        ip_address=(ip_address or "")[:45] or None,
    )
    db.add(auth_session)
    db.flush()  # assigns auth_session.id, which the access token references

    user.last_login_at = now
    ensure_preferences(db, user)

    access_token, access_expires = security.create_access_token(
        settings,
        user_id=user.id,
        workspace_id=user.workspace_id,
        session_id=auth_session.id,
        now=now,
    )

    return IssuedSession(
        access_token=access_token,
        access_expires_at=access_expires,
        refresh_token=raw_refresh,
        refresh_expires_at=auth_session.expires_at,
        user=user,
    )


def rotate_session(db: Session, settings: Settings, *, refresh_token: str) -> IssuedSession:
    """Exchange a refresh token for a new pair, invalidating the old one.

    Rotation makes a stolen refresh token single-use: whoever presents it second
    gets rejected.
    """
    now = datetime.now(UTC)
    token_hash = security.hash_refresh_token(refresh_token)

    auth_session = db.scalars(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == token_hash)
        .options(
            selectinload(AuthSession.user).selectinload(User.workspace),
            selectinload(AuthSession.user).selectinload(User.preferences),
        )
    ).first()

    if auth_session is None or not auth_session.is_usable(now):
        raise SessionExpiredError

    user = auth_session.user
    if not user.is_active:
        raise SessionExpiredError

    auth_session.revoked_at = now

    return issue_session(
        db,
        settings,
        user=user,
        # Preserve the original choice: a rotated token keeps the remaining
        # lifetime shape the user picked at login.
        remember_me=(auth_session.expires_at - auth_session.issued_at).days >= 1,
        user_agent=auth_session.user_agent,
        ip_address=auth_session.ip_address,
    )


def revoke_session(db: Session, *, refresh_token: str | None) -> None:
    """Log out. Silently succeeds if the token is already gone."""
    if not refresh_token:
        return
    token_hash = security.hash_refresh_token(refresh_token)
    auth_session = db.scalars(
        select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
    ).first()
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(UTC)
