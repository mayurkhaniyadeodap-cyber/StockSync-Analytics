"""Changing your own email or password, while signed in.

Both are authentication changes rather than profile edits, which is why neither
lives in the profile PATCH beside name and time zone: each one requires the
current password, and each one ends sessions or moves the login identity.

**Email changes are verified at the new address, not the old one.** The point of
the mail is to prove the person asking can receive at the address they are
moving to — sending it to the old one proves only what the session already
proved. Until that link is opened, the account's email is unchanged; the pending
address exists nowhere but inside the signed token, which is what keeps this out
of the schema.

Single use works the same way it does for a password reset: the token carries
the address it was issued *from*, so once the change lands the token no longer
describes the account and is dead. That also invalidates any other outstanding
change request, which is the behaviour you want when someone fires off three.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.models import AuthSession, User, normalize_email, utcnow
from app.services import mailer
from app.services.password_reset import MIN_PASSWORD_LENGTH, WeakPasswordError

log = logging.getLogger(__name__)

#: Marks the token as an email-change token and nothing else, so one signed with
#: the same secret cannot be presented to the password-reset endpoint.
TOKEN_TYPE = "email_change"  # noqa: S105 - a claim value, not a credential


class WrongPasswordError(AppError):
    """The current password did not match.

    Separate from `InvalidCredentialsError`, which is about signing in. Here the
    caller is already authenticated and is being asked to re-prove it, so the
    honest message is about this field rather than about the account.
    """

    code = "current_password_incorrect"
    status_code = 403
    message = "That is not your current password."
    next_step = "Re-enter your current password and try again."


class EmailTakenError(AppError):
    code = "email_taken"
    status_code = 409
    message = "Another account in this workspace already uses that address."
    next_step = "Choose a different address, or ask an administrator about the existing account."


class EmailUnchangedError(AppError):
    code = "email_unchanged"
    status_code = 422
    message = "That is already your email address."
    next_step = "Enter the address you want to move to."


class SamePasswordError(AppError):
    code = "password_unchanged"
    status_code = 422
    message = "That is already your password."
    next_step = "Choose a password you have not used here before."


class InvalidEmailTokenError(AppError):
    code = "email_token_invalid"
    status_code = 400
    message = "This verification link is no longer valid."
    next_step = "Request the change again from Settings."


def _require_password(user: User, password: str) -> None:
    """Re-prove the session belongs to whoever is at the keyboard.

    An access cookie is enough to read the dashboard. It is not enough to move
    the login identity or change the password, because a borrowed laptop has one
    and its owner's password is exactly what it does not have.
    """
    if not verify_password(user.password_hash, password):
        raise WrongPasswordError


def _taken_by_another(db: Session, user: User, normalized: str) -> bool:
    """Whether someone else in this workspace already holds that address.

    Scoped to the workspace because the unique constraint is: `(workspace_id,
    email_normalized)`. Checking globally would refuse an address that the
    database would have accepted.
    """
    existing = db.scalars(
        select(User).where(
            User.workspace_id == user.workspace_id,
            User.email_normalized == normalized,
        )
    ).first()
    return existing is not None and existing.id != user.id


# ---------------------------------------------------------------------------
# email
# ---------------------------------------------------------------------------


def request_email_change(
    db: Session, settings: Settings, *, user: User, new_email: str, current_password: str
) -> str:
    """Send a verification link to the proposed address. Returns that address.

    Nothing is written. The account keeps its current email until the link is
    opened, so abandoning the flow — or never receiving the mail — leaves the
    user exactly where they were.
    """
    _require_password(user, current_password)

    normalized = normalize_email(new_email)
    if normalized == user.email_normalized:
        raise EmailUnchangedError
    if _taken_by_another(db, user, normalized):
        raise EmailTakenError

    expires = datetime.now(UTC) + timedelta(minutes=settings.password_reset_ttl_minutes)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "typ": TOKEN_TYPE,
            # The address being moved to, and the one being moved from. The
            # second is the single-use binding: after the change it no longer
            # matches, so this token and every sibling stop working.
            "new": new_email.strip(),
            "old": user.email_normalized,
            "exp": expires,
            "jti": secrets.token_urlsafe(8),
        },
        settings.resolved_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    link = f"{settings.app_base_url.rstrip('/')}/verify-email?token={quote(token)}"
    mailer.send(
        settings,
        to=new_email.strip(),
        subject="Confirm your new StockSync Analytics email address",
        body=(
            f"Hello {user.full_name or user.email},\n\n"
            "This address was given as the new sign-in email for a StockSync "
            "Analytics account. Opening this link completes the change:\n\n"
            f"{link}\n\n"
            f"The link works once and expires in {settings.password_reset_ttl_minutes} minutes. "
            "Until then the account keeps its current address.\n\n"
            "If you were not expecting this, ignore it — nothing has changed, "
            "and this link cannot be used to sign in.\n"
        ),
    )
    log.info("email change requested for user=%s", user.id)
    return new_email.strip()


def confirm_email_change(db: Session, settings: Settings, *, token: str) -> User:
    """Complete a change from its verification link.

    Unauthenticated on purpose: the link is opened in whichever browser reads
    the new mailbox, which is often not the one that asked. The token is the
    authority, and it only works while the account still looks the way it did
    when the token was signed.
    """
    try:
        claims = jwt.decode(
            token, settings.resolved_jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise InvalidEmailTokenError from exc

    if claims.get("typ") != TOKEN_TYPE:
        raise InvalidEmailTokenError

    try:
        user = db.get(User, int(claims.get("sub", "")))
    except (TypeError, ValueError) as exc:
        raise InvalidEmailTokenError from exc

    if user is None or not user.is_active:
        raise InvalidEmailTokenError

    # The single-use check: the address this token was issued against.
    if claims.get("old") != user.email_normalized:
        raise InvalidEmailTokenError

    new_email = str(claims.get("new", "")).strip()
    normalized = normalize_email(new_email)
    if not new_email or not normalized:
        raise InvalidEmailTokenError

    # Checked again here, not only when the link was requested: someone else in
    # the workspace may have taken the address in between, and the unique
    # constraint would otherwise surface as a 500.
    if _taken_by_another(db, user, normalized):
        raise EmailTakenError

    user.email = new_email
    user.email_normalized = normalized
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)

    log.info("email change confirmed for user=%s", user.id)
    return user


# ---------------------------------------------------------------------------
# password
# ---------------------------------------------------------------------------


def change_password(db: Session, *, user: User, current_password: str, new_password: str) -> int:
    """Set a new password and revoke every session. Returns how many were cut.

    Every session, including the caller's own: the route issues a fresh one
    afterwards, so the person doing this stays signed in while everything else
    that held the old password's sessions does not. Leaving them alive would
    make a password change no protection against the thing it is usually done
    about.
    """
    _require_password(user, current_password)

    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError
    if new_password == current_password:
        raise SamePasswordError

    user.password_hash = hash_password(new_password)
    user.updated_at = utcnow()

    revoked = 0
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
        if session.revoked_at is None:
            session.revoked_at = user.updated_at
            revoked += 1

    db.commit()
    log.info("password changed for user=%s, %s session(s) revoked", user.id, revoked)
    return revoked
