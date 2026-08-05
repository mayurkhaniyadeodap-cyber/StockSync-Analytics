"""Security response headers.

The application authenticates with cookies, which makes two of these load-bearing
rather than decorative:

* **Framing.** A page that can be put in an iframe by another origin can be
  clickjacked, and a cookie-authenticated session is exactly what that attack
  is for. `frame-ancestors 'none'` is the modern control; `X-Frame-Options` is
  sent alongside it for browsers that predate CSP Level 2.
* **MIME sniffing.** Report downloads are user-supplied data in a file the
  browser is asked to save. Without `nosniff` a browser may decide a CSV is
  HTML and run it.

`Strict-Transport-Security` is conditional on `cookie_secure`. Sending HSTS from
a deployment that serves plain HTTP would pin every visitor's browser to a
scheme the server does not answer on, and the only cure is waiting out
`max-age` — so it is sent when TLS is actually in use and not before.

The CSP is deliberately narrow. This API serves JSON and files, never HTML: the
single-page application is served by the proxy, not by uvicorn. A policy that
forbids everything is therefore correct here and would not be correct if this
process ever started returning pages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings

#: Applied to every response, including errors.
#:
#: `Referrer-Policy` is `same-origin` rather than `no-referrer`: internal
#: navigation keeps a referrer that is useful in the access log, and nothing
#: leaves for a third party. `Permissions-Policy` turns off the device APIs this
#: application has no use for, so a compromised script cannot reach them.
BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the headers above to every response.

    Set rather than overwritten blindly: a route that has deliberately chosen a
    different value for one of these keeps it. Nothing does today, and the day
    something does, the route is the more specific statement.
    """

    def __init__(self, app: object, *, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._headers = dict(BASE_HEADERS)
        if settings.cookie_secure and settings.hsts_max_age_seconds > 0:
            self._headers["Strict-Transport-Security"] = (
                f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
            )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in self._headers.items():
            response.headers.setdefault(name, value)
        return response
