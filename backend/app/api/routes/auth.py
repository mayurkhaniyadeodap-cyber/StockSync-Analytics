"""Authentication endpoints.

Tokens travel in httpOnly cookies, never in the response body — JavaScript
cannot read them, so an XSS bug cannot exfiltrate a session.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response, status

from app.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    DbDep,
    SettingsDep,
)
from app.config import Settings
from app.core.errors import TooManyAttemptsError
from app.core.throttle import get_login_throttle
from app.core.throttle import keys_for as throttle_keys
from app.schemas.auth import CurrentUser as CurrentUserSchema
from app.schemas.auth import LoginRequest, PreferencesUpdate, ProfileUpdate
from app.services import auth as auth_service
from app.services.auth import InvalidCredentialsError


def _redacted(email: str) -> str:
    """An address the log can carry: enough to correlate, not the whole thing.

    A failed-login log full of complete addresses is a list of valid accounts
    for whoever reads the logs.
    """
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# The refresh cookie is only ever sent to the endpoints that need it, so it is
# not attached to every API call.
REFRESH_PATH = "/api/auth"


def _set_auth_cookies(
    response: Response,
    settings: Settings,
    issued: auth_service.IssuedSession,
    *,
    remember_me: bool,
) -> None:
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        # "lax" still sends the cookie on top-level navigation back to the app
        # while blocking cross-site POSTs, which is the CSRF case that matters
        # here. "strict" would log the user out whenever they follow a link in.
        "samesite": "lax",
        "domain": settings.cookie_domain,
    }

    response.set_cookie(
        ACCESS_COOKIE,
        issued.access_token,
        max_age=settings.access_token_minutes * 60,
        path="/",
        **common,  # type: ignore[arg-type]
    )

    # Without max_age the browser treats it as a session cookie and drops it when
    # the window closes — which is exactly what "Keep me signed in" unchecked
    # should mean.
    refresh_max_age = (
        int((issued.refresh_expires_at - issued.access_expires_at).total_seconds())
        + settings.access_token_minutes * 60
        if remember_me
        else None
    )
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=refresh_max_age,
        path=REFRESH_PATH,
        **common,  # type: ignore[arg-type]
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_PATH)):
        response.delete_cookie(
            name,
            path=path,
            domain=settings.cookie_domain,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )


def _to_schema(user: object, access_expires_at: datetime | None = None) -> CurrentUserSchema:
    """The user, plus when their access token stops working if we know it.

    Only the endpoints that issue a token or read one's claims can fill it in;
    the rest send null and the client falls back to recovering from the 401.
    """
    return CurrentUserSchema.model_validate(user).model_copy(
        update={"access_expires_at": access_expires_at}
    )


@router.post("/auth/login", response_model=CurrentUserSchema, summary="Sign in")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> CurrentUserSchema:
    # Throttled on the account *and* the client address — see core.throttle for
    # why one key is not enough. Checked before the password is verified, so a
    # locked-out attacker cannot even make us pay argon2's 95 ms.
    throttle = get_login_throttle(settings)
    keys = throttle_keys(payload.email, request.client.host if request.client else None)
    decision = throttle.check(keys)
    if not decision.allowed:
        log.warning("login refused by throttle for %s", _redacted(payload.email))
        raise TooManyAttemptsError(decision.retry_after_seconds)

    try:
        user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    except InvalidCredentialsError:
        throttle.record_failure(keys)
        throttle.prune()
        raise

    # Correct password: the account is no longer under suspicion. The address
    # key is cleared with it, because the person at that address just proved
    # they are a legitimate user of this system.
    throttle.record_success(keys)

    issued = auth_service.issue_session(
        db,
        settings,
        user=user,
        remember_me=payload.remember_me,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(user)

    _set_auth_cookies(response, settings, issued, remember_me=payload.remember_me)
    # Email, never the password or token — the redaction filter would catch a
    # token, but the safer habit is not to hand it to the logger at all.
    log.info("login succeeded for user_id=%s", user.id)
    return _to_schema(user, issued.access_expires_at)


@router.post("/auth/refresh", response_model=CurrentUserSchema, summary="Rotate the session")
def refresh(
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> CurrentUserSchema:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise auth_service.SessionExpiredError

    issued = auth_service.rotate_session(db, settings, refresh_token=token)
    db.commit()
    db.refresh(issued.user)

    remember_me = (issued.refresh_expires_at - issued.access_expires_at).days >= 1
    _set_auth_cookies(response, settings, issued, remember_me=remember_me)
    return _to_schema(issued.user, issued.access_expires_at)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out and revoke the session",
)
def logout(request: Request, response: Response, db: DbDep, settings: SettingsDep) -> None:
    auth_service.revoke_session(db, refresh_token=request.cookies.get(REFRESH_COOKIE))
    db.commit()
    _clear_auth_cookies(response, settings)


@router.get("/auth/me", response_model=CurrentUserSchema, summary="The signed-in user")
def me(request: Request, user: CurrentUser) -> CurrentUserSchema:
    """Who is signed in, and how long this access token has left.

    The expiry is what lets a tab that has been open for hours renew the
    session before a request fails, rather than discovering the problem as a
    401 in the middle of something the user was doing.
    """
    return _to_schema(user, getattr(request.state, "access_expires_at", None))


@router.patch(
    "/me/preferences",
    response_model=CurrentUserSchema,
    summary="Update display preferences",
)
def update_preferences(
    payload: PreferencesUpdate,
    user: CurrentUser,
    db: DbDep,
) -> CurrentUserSchema:
    """Design doc §13: each field saves on its own and confirms inline."""
    preferences = auth_service.ensure_preferences(db, user)
    changes = payload.model_dump(exclude_unset=True)

    # Not a display preference of this user's: it sets the line everyone's Low
    # stock figure is drawn at, so it lives on the workspace.
    threshold = changes.pop("low_stock_threshold", None)
    if threshold is not None:
        user.workspace.low_stock_threshold = threshold

    for field, value in changes.items():
        if value is not None:
            setattr(preferences, field, value)

    db.commit()
    db.refresh(user)
    return _to_schema(user)


@router.patch(
    "/me",
    response_model=CurrentUserSchema,
    summary="Update your profile",
)
def update_profile(
    payload: ProfileUpdate,
    user: CurrentUser,
    db: DbDep,
) -> CurrentUserSchema:
    """Name and time zone. Everything else about a user is set elsewhere."""
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, field, value.strip() if isinstance(value, str) else value)

    db.commit()
    db.refresh(user)
    return _to_schema(user)
