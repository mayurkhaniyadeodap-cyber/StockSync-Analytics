"""Error envelope.

Design doc §16: an error says what happened *and what to do next*. That is hard
to enforce if the "next step" is invented in the UI, so it travels with the
error from the server:

    {
      "error": {
        "code": "shopify_rate_limited",
        "message": "Shopify is temporarily limiting requests.",
        "next": "StockSync Analytics will retry on its own in about 4 minutes.",
        "detail": {"retry_after_seconds": 240}
      }
    }

``message`` states the fact, ``next`` states the action. Neither apologises.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for every error StockSync Analytics raises deliberately.

    Subclasses set ``code``, ``status_code`` and a default ``next_step`` so the
    same failure always reads the same way wherever it surfaces.
    """

    code: str = "internal_error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "Something went wrong on our side."
    next_step: str = "Try again. If it keeps happening, contact your workspace admin."

    def __init__(
        self,
        message: str | None = None,
        *,
        next_step: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.next_step = next_step or self.next_step
        self.detail = detail or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "next": self.next_step,
        }
        if self.detail:
            payload["detail"] = self.detail
        return {"error": payload}


class ServiceUnavailableError(AppError):
    code = "service_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "StockSync Analytics can't reach a service it depends on."
    next_step = "Wait a moment and retry. If it persists, check the service is running."


class TooManyAttemptsError(AppError):
    """Too many failed sign-ins from this account or this address.

    Deliberately says *how long* rather than how many attempts remain. A
    countdown of remaining attempts tells an attacker exactly how hard they may
    push; a wait tells the honest user the one thing they need.
    """

    code = "too_many_attempts"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Too many sign-in attempts."
    next_step = "Wait a few minutes and try again."

    def __init__(self, retry_after_seconds: int) -> None:
        minutes = max(1, round(retry_after_seconds / 60))
        unit = "minute" if minutes == 1 else "minutes"
        super().__init__(
            "Too many sign-in attempts.",
            next_step=f"Try again in about {minutes} {unit}.",
        )
        self.retry_after_seconds = retry_after_seconds


class RateLimitedError(AppError):
    """Too many expensive operations started by one user in a short window.

    Distinct from `TooManyAttemptsError`, which is about suspicion. This one is
    about capacity: nothing is wrong, the user is simply ahead of the single
    worker thread. So it names the operation and says when — a message that
    reads as "wait" rather than "you have been locked out".
    """

    code = "rate_limited"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "That's more of these than StockSync Analytics can start at once."
    next_step = "Wait a moment and try again."

    def __init__(self, what: str, retry_after_seconds: int) -> None:
        if retry_after_seconds < 90:
            when = f"{max(1, retry_after_seconds)} seconds"
        else:
            minutes = round(retry_after_seconds / 60)
            when = f"{minutes} minute" + ("" if minutes == 1 else "s")
        super().__init__(
            f"You've started several {what}s in a row.",
            next_step=f"Wait about {when} and try again. The one already running is unaffected.",
        )
        self.retry_after_seconds = retry_after_seconds


def _envelope(
    status_code: int, code: str, message: str, next_step: str, detail: Any = None
) -> JSONResponse:
    payload: dict[str, Any] = {"code": code, "message": message, "next": next_step}
    if detail:
        payload["detail"] = detail
    return JSONResponse(status_code=status_code, content={"error": payload})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so *every* error leaves the API in the same shape."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("%s: %s", exc.code, exc.message, exc_info=exc)
        response = JSONResponse(status_code=exc.status_code, content=exc.to_payload())
        # A 429 that does not say when to come back leaves the client guessing,
        # and a guessing client retries in a loop.
        retry_after = getattr(exc, "retry_after_seconds", None)
        if retry_after:
            response.headers["Retry-After"] = str(int(retry_after))
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in err["loc"][1:]) or "body",
                "problem": err["msg"],
            }
            for err in exc.errors()
        ]
        return _envelope(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_request",
            "Some of the values sent weren't valid.",
            "Correct the highlighted fields and submit again.",
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        known: dict[int, tuple[str, str, str]] = {
            401: (
                "not_authenticated",
                "You're not signed in.",
                "Sign in and try again.",
            ),
            403: (
                "not_permitted",
                "You don't have access to this.",
                "Ask your workspace admin if you need it.",
            ),
            404: (
                "not_found",
                "That doesn't exist.",
                "Check the link, or go back to the dashboard.",
            ),
            405: (
                "method_not_allowed",
                "That action isn't available here.",
                "Go back and try a different action.",
            ),
        }
        code, message, next_step = known.get(
            exc.status_code,
            ("request_failed", str(exc.detail), "Try again."),
        )
        return _envelope(exc.status_code, code, message, next_step)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Deliberately no exception text in the response — it can carry
        # connection strings and tokens. It goes to the (redacting) log instead.
        log.exception("unhandled exception", exc_info=exc)
        return _envelope(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Something went wrong on our side.",
            "Try again. If it keeps happening, contact your workspace admin.",
        )
