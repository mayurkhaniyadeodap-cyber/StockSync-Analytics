"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.core import security
from app.core.errors import AppError
from app.db.session import get_db
from app.models import AuthSession, User
from app.models.base import utcnow

ACCESS_COOKIE = "stocksync_access"
REFRESH_COOKIE = "stocksync_refresh"


class NotAuthenticatedError(AppError):
    code = "not_authenticated"
    status_code = 401
    message = "You're not signed in."
    next_step = "Sign in and try again."


def get_app_settings(request: Request) -> Settings:
    """The settings the app was built with, not the cached global."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbDep, settings: SettingsDep) -> User:
    """Resolve the signed-in user from the access cookie.

    The token is verified *and* its backing session re-checked on every request.
    Signature alone is not enough: a token stays cryptographically valid until it
    expires, so without the session lookup "Log out" would leave a working
    credential in the wild for up to 15 minutes.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise NotAuthenticatedError

    try:
        claims = security.decode_access_token(settings, token)
    except security.TokenError as exc:
        raise NotAuthenticatedError from exc

    # Stashed so `/auth/me` can tell the client when this token dies, without
    # decoding it a second time. The client cannot read the httpOnly cookie, so
    # without this it would have to guess `access_token_minutes` — and a guess
    # that drifts from the server's setting is how sessions lapse mid-use.
    request.state.access_expires_at = claims.expires_at

    auth_session = db.get(AuthSession, claims.session_id)
    if auth_session is None or not auth_session.is_usable(utcnow()):
        raise NotAuthenticatedError

    user = db.scalars(
        select(User)
        .where(User.id == claims.user_id)
        .options(selectinload(User.workspace), selectinload(User.preferences))
    ).first()

    if user is None or not user.is_active or user.workspace_id != claims.workspace_id:
        raise NotAuthenticatedError

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
