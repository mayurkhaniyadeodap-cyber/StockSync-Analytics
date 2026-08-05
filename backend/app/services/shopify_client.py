"""The Shopify Admin API, as one seam.

Everything that talks to Shopify goes through here, so retries, pagination and
error mapping are decided once and the sync service reads as a sequence of
steps rather than a sequence of HTTP calls. It is also the single place tests
stub — `httpx.get` — which is why nothing above this module imports httpx.

Two things Shopify does that the caller must not have to think about:

* **Cursor pagination.** The Admin REST API does not take a page number. The
  next page arrives as a ``page_info`` cursor inside a ``Link`` header, and the
  cursor is opaque and single-use. ``paginate`` yields pages and hands back the
  cursor for each, so a run interrupted halfway can resume from it.
* **Leaky-bucket rate limiting.** A 429 is normal operation under load, not a
  fault. It carries ``Retry-After`` and is retried here rather than surfacing
  to the user as a failed sync.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings
from app.core.errors import AppError

log = logging.getLogger(__name__)

# 250 is the Admin API's per-page maximum. Fewer requests means fewer chances
# to hit the rate limit and a shorter total sync.
PAGE_SIZE = 250

# A 429 under a leaky bucket clears in seconds. Three attempts covers a normal
# burst; a fourth would mean something is wrong that waiting will not fix.
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 2.0

_NEXT_LINK = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')
_PAGE_INFO = re.compile(r"[?&]page_info=(?P<cursor>[^&>]+)")


class ShopifyError(AppError):
    """Base for anything that went wrong talking to Shopify."""

    code = "shopify_error"
    status_code = 502
    message = "StockSync Analytics couldn't complete a Shopify request."
    next_step = "Try again in a moment."


class ShopifyAuthError(ShopifyError):
    code = "shopify_auth_failed"
    status_code = 400
    message = "Connection failed — check the store URL and token permissions."
    next_step = "Confirm the Admin API access token is correct and hasn't been revoked."


class ShopifyScopeError(ShopifyError):
    code = "shopify_missing_scopes"
    status_code = 400
    message = "That token doesn't have the access StockSync Analytics needs."
    next_step = "Grant read_orders to the app in its Configuration, then paste a new token."


class ShopifyUnreachableError(ShopifyError):
    code = "shopify_unreachable"
    status_code = 502
    message = "StockSync Analytics couldn't reach Shopify."
    next_step = "Check the store URL and your connection, then try again."


class ShopifyRateLimitedError(ShopifyError):
    code = "shopify_rate_limited"
    status_code = 429
    message = "Shopify is temporarily limiting requests."
    next_step = "StockSync Analytics will retry on its own shortly."


@dataclass(frozen=True)
class Page:
    """One page of results, with the cursor that would fetch the next."""

    items: list[dict[str, Any]]
    next_cursor: str | None
    #: Cursor this page was fetched with — what a resume would replay.
    cursor: str | None = None


@dataclass
class ShopifyClient:
    """Authenticated access to one store."""

    settings: Settings
    shop_domain: str
    token: str
    #: Set by tests to make retry waits instant.
    sleep: Any = field(default=time.sleep)

    # -- low level ---------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.settings.shopify_api_version}/{path}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """One authenticated GET, with rate limiting absorbed."""
        url = path if path.startswith("https://") else self._url(path)

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = httpx.get(
                    url,
                    params=params,
                    headers={"X-Shopify-Access-Token": self.token, "Accept": "application/json"},
                    timeout=self.settings.shopify_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                # DNS, TLS and timeout are one problem from the user's side:
                # the store could not be reached.
                log.warning(
                    "shopify request failed for %s: %s", self.shop_domain, type(exc).__name__
                )
                raise ShopifyUnreachableError from exc

            if response.status_code == 429:
                wait = _retry_after(response)
                if attempt < MAX_RATE_LIMIT_RETRIES:
                    log.info("shopify rate limited, waiting %.1fs (attempt %s)", wait, attempt + 1)
                    self.sleep(wait)
                    continue
                raise ShopifyRateLimitedError(detail={"retry_after_seconds": int(wait)})

            return _raise_for_status(response)

        raise ShopifyRateLimitedError  # pragma: no cover - loop always returns or raises

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.get(path, params)
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ShopifyUnreachableError from exc
        return payload

    # -- pagination --------------------------------------------------------

    def paginate(
        self,
        path: str,
        key: str,
        *,
        params: dict[str, Any] | None = None,
        start_cursor: str | None = None,
    ) -> Iterator[Page]:
        """Yield pages of ``key`` until Shopify stops offering a next link.

        Shopify rejects a request that sends ``page_info`` alongside the
        filters that produced it — the cursor already encodes them. So the
        first request carries the filters and every subsequent one carries only
        the cursor and the limit.
        """
        cursor = start_cursor

        while True:
            if cursor:
                query: dict[str, Any] = {"limit": PAGE_SIZE, "page_info": cursor}
            else:
                query = {"limit": PAGE_SIZE, **(params or {})}

            response = self.get(path, query)
            try:
                body: dict[str, Any] = response.json()
            except ValueError as exc:
                raise ShopifyUnreachableError from exc

            items = body.get(key) or []
            next_cursor = _next_cursor(response)
            yield Page(items=list(items), next_cursor=next_cursor, cursor=cursor)

            if not next_cursor:
                return
            cursor = next_cursor

    # -- specific resources ------------------------------------------------

    def shop(self) -> dict[str, Any]:
        return self.get_json("shop.json").get("shop") or {}

    def access_scopes(self) -> list[str]:
        """Granted scopes, or an empty list if the token cannot read them.

        Some token types cannot introspect themselves. That is not fatal — a
        successful ``shop.json`` already proved authentication — so an unknown
        scope list is reported as unknown rather than as a failure.
        """
        try:
            body = self.get_json("../../oauth/access_scopes.json")
        except ShopifyError:
            log.info("could not read access scopes for %s", self.shop_domain)
            return []
        return [str(s.get("handle", "")) for s in body.get("access_scopes", [])]

    def newest_order_at(self) -> str | None:
        """When the newest order in the store was processed, as an ISO string.

        One order, one request — the cheapest question that can tell us whether
        the database is behind the store. Returns None rather than raising: not
        knowing how fresh we are must never break a page that would otherwise
        render.
        """
        try:
            body = self.get_json(
                "orders.json",
                {"limit": 1, "status": "any", "order": "processed_at desc"},
            )
        except ShopifyError:
            log.info("could not read the newest order for %s", self.shop_domain)
            return None
        orders = body.get("orders") or []
        if not orders:
            return None
        newest = orders[0]
        value = newest.get("processed_at") or newest.get("created_at")
        return str(value) if value else None

    def orders(self, *, since: str, start_cursor: str | None = None) -> Iterator[Page]:
        return self.paginate(
            "orders.json",
            "orders",
            params={
                # Without status=any Shopify returns open orders only, which
                # silently omits every completed sale — the opposite of what a
                # sales figure needs.
                "status": "any",
                "created_at_min": since,
            },
            start_cursor=start_cursor,
        )

    def count(self, path: str, params: dict[str, Any] | None = None) -> int | None:
        """A resource count, for turning progress into a percentage.

        Returns None rather than raising: a missing count costs a progress bar
        its precision, which is not worth failing a sync over.
        """
        try:
            return int(self.get_json(path, params).get("count", 0))
        except (ShopifyError, ValueError, TypeError):
            return None


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    try:
        return max(float(raw), 0.1) if raw else DEFAULT_RETRY_AFTER_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


def _raise_for_status(response: httpx.Response) -> httpx.Response:
    if response.status_code in (401, 403):
        # 403 on a specific resource means the token authenticates but lacks
        # the scope for it — a different fix from a bad token, so a different
        # error.
        if response.status_code == 403:
            raise ShopifyScopeError
        raise ShopifyAuthError
    if response.status_code == 404:
        raise ShopifyAuthError(
            "That store doesn't exist, or the token isn't valid for it.",
            next_step="Check the store URL, then confirm the token belongs to that store.",
        )
    if response.status_code >= 400:
        log.warning("shopify returned %s for %s", response.status_code, response.url)
        raise ShopifyUnreachableError
    return response


def _next_cursor(response: httpx.Response) -> str | None:
    """Pull page_info out of the Link header, if there is a next page."""
    link = response.headers.get("Link") or response.headers.get("link")
    if not link:
        return None
    match = _NEXT_LINK.search(link)
    if not match:
        return None
    found = _PAGE_INFO.search(match.group("url"))
    return found.group("cursor") if found else None
