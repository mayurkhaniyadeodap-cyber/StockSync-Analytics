"""Shopify store connection: validate a credential, store it, revoke it.

M2 scope is the credential only — proving a store URL and Admin API token work
together, and keeping them safely. Pulling products and orders is a later
milestone and deliberately absent.

The token is verified against Shopify before it is ever stored. Saving first
and discovering on the next sync that the token was wrong would put the error
hours away from the action that caused it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import crypto
from app.core.errors import AppError
from app.models import ShopifyConnection, User, utcnow
from app.repositories import ShopifyConnectionRepository

log = logging.getLogger(__name__)

# A myshopify domain is a single lowercase label. Custom domains are not
# accepted: the Admin API is only addressable at the myshopify host, so taking
# "shop.example.com" would produce a confusing 404 later instead of a clear
# rejection now.
SHOP_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

# The URL the browser shows while you are inside the Shopify admin. It does not
# contain the myshopify host at all, so the store handle is lifted out of the
# path instead.
ADMIN_URL = re.compile(r"^admin\.shopify\.com/store/(?P<store>[a-z0-9][a-z0-9-]*)(?:[/?].*)?$")

# The one scope the product needs. Checked at connect time so a token that
# cannot do the job is rejected while the user is still on the form.
#
# **`read_products` was removed here, not merely stopped being used.** Orders
# carry `sku_at_sale`, so sales attribute to an uploaded SKU without the
# catalogue ever being fetched — that is why the catalogue was dropped. Leaving
# the scope in this tuple meant a token with every scope the app actually calls
# was still rejected as deficient: Test Connection and Verify both failed, the
# stored connection sat at `token_expired`, and store name, plan, currency and
# scopes could never be recorded. The requirement outlived the requirement's
# reason by one refactor.
REQUIRED_SCOPES = ("read_orders",)


class InvalidShopDomainError(AppError):
    code = "invalid_shop_domain"
    status_code = 422
    message = "That doesn't look like a Shopify store URL."
    next_step = "Use the myshopify.com address, for example mystore.myshopify.com."


class ShopifyAuthError(AppError):
    code = "shopify_auth_failed"
    status_code = 400
    message = "Connection failed — check the store URL and token permissions."
    next_step = "Confirm the Admin API access token is correct and hasn't been revoked."


class ShopifyScopeError(AppError):
    code = "shopify_missing_scopes"
    status_code = 400
    message = "That token doesn't have the access StockSync Analytics needs."
    next_step = "Grant read_orders to the app in its Configuration, then paste a new token."


class ShopifyUnreachableError(AppError):
    code = "shopify_unreachable"
    status_code = 502
    message = "StockSync Analytics couldn't reach Shopify."
    next_step = "Check the store URL and your connection, then try again."


class ShopifyRateLimitedError(AppError):
    code = "shopify_rate_limited"
    status_code = 429
    message = "Shopify is temporarily limiting requests."
    next_step = "Wait about a minute and try again."


class NotConnectedError(AppError):
    code = "shopify_not_connected"
    status_code = 404
    message = "No Shopify store is connected."
    next_step = "Connect a store first."


class EnvCredentialError(AppError):
    """An action was attempted that only makes sense for a stored connection."""

    code = "shopify_env_credential"
    status_code = 409
    message = "This store is configured in .env, not in StockSync Analytics."
    next_step = (
        "Remove SHOPIFY_STORE_URL and SHOPIFY_ADMIN_API_TOKEN from .env and restart, "
        "or connect the store here to store it encrypted instead."
    )


@dataclass(frozen=True)
class ShopProfile:
    """What Shopify says about the store — §9.2 step 3's confirmation card."""

    shop_domain: str
    store_name: str | None
    plan_name: str | None
    currency: str | None
    scopes: list[str]


@dataclass(frozen=True)
class ResolvedCredential:
    """A usable credential and where it came from."""

    shop_domain: str
    token: str
    source: str  # "database" | "environment"


def resolve_credential(
    db: Session, settings: Settings, *, workspace_id: int
) -> ResolvedCredential | None:
    """The credential to use, or None if the workspace has no usable one.

    A connection stored through the UI wins over ``.env``. That ordering is
    what makes the fallback safe to leave configured: connecting a store here
    takes effect immediately without anyone having to remember that a stale
    development value is sitting in a file, and it cannot be silently
    overridden by one.
    """
    connection = ShopifyConnectionRepository(db).get(workspace_id)
    if connection is not None and connection.status == "connected":
        return ResolvedCredential(
            shop_domain=connection.shop_domain,
            token=crypto.decrypt(settings, connection.access_token_encrypted),
            source="database",
        )

    if settings.env_shopify_credential_active:
        return ResolvedCredential(
            shop_domain=normalize_shop_domain(settings.shopify_store_url),
            token=settings.shopify_admin_api_token.strip(),
            source="environment",
        )

    return None


def normalize_shop_domain(value: str) -> str:
    """Accept what people paste; store one canonical form.

    Users copy the URL out of the browser bar far more often than they type the
    bare host, and the modern Shopify admin lives at
    ``admin.shopify.com/store/<name>`` — the store's own myshopify host is never
    displayed while you are working in it. Rejecting the address the user is
    actually looking at, in favour of one they have to go and find, is a bad
    trade for a field that can recover it in one line.
    """
    text = value.strip().lower()
    text = re.sub(r"^https?://", "", text)

    # admin.shopify.com/store/<name>[/...]  ->  <name>
    admin = ADMIN_URL.match(text)
    if admin:
        text = admin.group("store")
    else:
        text = text.split("/", 1)[0].split("?", 1)[0].strip()

    if not text:
        raise InvalidShopDomainError
    # A bare store name is unambiguous and worth completing.
    if "." not in text:
        text = f"{text}.myshopify.com"
    if not SHOP_DOMAIN.match(text):
        raise InvalidShopDomainError(detail={"received": value[:120]})
    return text


def _admin_url(settings: Settings, shop_domain: str, path: str) -> str:
    return f"https://{shop_domain}/admin/api/{settings.shopify_api_version}/{path}"


def _request(settings: Settings, shop_domain: str, token: str, path: str) -> dict[str, Any]:
    """One authenticated Admin API GET, with Shopify's failures mapped to ours."""
    try:
        response = httpx.get(
            _admin_url(settings, shop_domain, path),
            headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
            timeout=settings.shopify_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        # Covers DNS failure, TLS failure and timeout — from the user's side
        # these are one problem: the store could not be reached.
        log.warning("shopify request failed for %s: %s", shop_domain, type(exc).__name__)
        raise ShopifyUnreachableError from exc

    if response.status_code in (401, 403):
        raise ShopifyAuthError
    if response.status_code == 404:
        # The host resolved but the API did not answer for it, which in practice
        # means the store name is wrong.
        raise ShopifyAuthError(
            "That store doesn't exist, or the token isn't valid for it.",
            next_step="Check the store URL, then confirm the token belongs to that store.",
        )
    if response.status_code == 429:
        raise ShopifyRateLimitedError(
            detail={"retry_after_seconds": int(response.headers.get("Retry-After", "60") or 60)}
        )
    if response.status_code >= 400:
        log.warning("shopify %s returned %s", path, response.status_code)
        raise ShopifyUnreachableError

    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise ShopifyUnreachableError from exc
    return payload


def fetch_profile(settings: Settings, *, shop_domain: str, token: str) -> ShopProfile:
    """Prove the credential works and report what it can see.

    Two calls on purpose. ``shop.json`` proves the token authenticates and
    returns the details the confirmation card shows; the access-scopes endpoint
    proves it can do the work, which a successful shop.json alone does not.
    """
    shop = _request(settings, shop_domain, token, "shop.json").get("shop") or {}

    scopes: list[str] = []
    try:
        granted = _request(settings, shop_domain, token, "../../oauth/access_scopes.json")
        scopes = [str(s.get("handle", "")) for s in granted.get("access_scopes", [])]
    except AppError:
        # Some token types cannot read their own scopes. Not fatal: shop.json
        # already proved authentication, so treat the scope list as unknown
        # rather than failing a working connection.
        log.info("could not read access scopes for %s", shop_domain)

    if scopes:
        missing = [s for s in REQUIRED_SCOPES if s not in scopes]
        if missing:
            raise ShopifyScopeError(detail={"missing_scopes": missing, "granted_scopes": scopes})

    return ShopProfile(
        shop_domain=shop_domain,
        store_name=(shop.get("name") or None),
        plan_name=(shop.get("plan_display_name") or shop.get("plan_name") or None),
        currency=(shop.get("currency") or None),
        scopes=scopes,
    )


def test_connection(settings: Settings, *, shop_url: str, token: str) -> ShopProfile:
    """The Test Connection button. Touches Shopify, writes nothing."""
    return fetch_profile(settings, shop_domain=normalize_shop_domain(shop_url), token=token)


def save_connection(
    db: Session,
    settings: Settings,
    *,
    workspace_id: int,
    user: User,
    shop_url: str,
    token: str,
) -> tuple[ShopifyConnection, ShopProfile]:
    """Validate against Shopify, then store the credential encrypted."""
    profile = test_connection(settings, shop_url=shop_url, token=token)

    connections = ShopifyConnectionRepository(db)
    connection = connections.get(workspace_id)
    now = utcnow()

    if connection is None:
        connection = connections.add(
            ShopifyConnection(
                workspace_id=workspace_id,
                shop_domain=profile.shop_domain,
                access_token_encrypted=crypto.encrypt(settings, token),
                connected_at=now,
            )
        )
    else:
        connection.shop_domain = profile.shop_domain
        connection.access_token_encrypted = crypto.encrypt(settings, token)
        # Reconnecting after a disconnect starts a new connected period.
        if connection.connected_at is None or connection.status != "connected":
            connection.connected_at = now
        connection.disconnected_at = None

    connection.store_name = profile.store_name
    connection.plan_name = profile.plan_name
    connection.currency = profile.currency
    connection.token_scopes = ",".join(profile.scopes) or None
    connection.status = "connected"
    connection.last_verified_at = now
    connection.connected_by = user.id

    # Domain and status only. The token never goes near the logger.
    log.info("shopify connected workspace=%s shop=%s", workspace_id, profile.shop_domain)
    return connection, profile


def adopt_env_credential(
    db: Session, settings: Settings, *, workspace_id: int, user_id: int | None
) -> ShopifyConnection | None:
    """Promote an ``.env``-configured store to a stored connection.

    Synced rows carry a ``connection_id`` foreign key, so a sync needs a
    connection row to point at — an environment-only store has none. Rather
    than refuse to sync a store the app is otherwise treating as connected,
    the credential is written once, encrypted, exactly as the Connect form
    would have written it.

    This is visible, not silent: the page immediately reports
    ``source: database`` afterwards, and from then on the documented
    precedence applies — the stored copy wins and editing ``.env`` no longer
    changes anything. That is the same rule, not an exception to it.

    **The credential is verified before it is stored.** It previously was not:
    the row went in with ``status="connected"`` and ``last_verified_at`` stamped
    without a single call to Shopify. A token missing ``read_orders`` then
    looked like a healthy connection while every product stage 403'd, and the
    Connection page could not show which scopes were granted because none had
    ever been read. Claiming a verification that did not happen is the part
    that made a scope problem invisible for the life of the project.

    A missing scope does not stop the adoption. The token can still be good for
    part of the job — this store's is: orders sync, products cannot — and
    refusing to adopt would turn a partial capability into no capability at all.
    What changes is that the granted scopes are recorded, so the page can show
    what is actually available and the sync's error is corroborated rather than
    contradicted by a green badge.

    Returns None when there is nothing to adopt.
    """
    if not settings.env_shopify_credential_active:
        return None

    connections = ShopifyConnectionRepository(db)
    if connections.get(workspace_id) is not None:
        return None

    domain = normalize_shop_domain(settings.shopify_store_url)
    token = settings.shopify_admin_api_token.strip()

    profile: ShopProfile | None = None
    granted: list[str] = []
    status = "connected"
    try:
        profile = fetch_profile(settings, shop_domain=domain, token=token)
        granted = profile.scopes
    except ShopifyScopeError as exc:
        # Authenticates, but cannot do all of the work. The scope list travels
        # on the error, so it can still be recorded.
        detail = exc.detail or {}
        granted = [str(s) for s in detail.get("granted_scopes", [])]
        status = "missing_scopes"
        log.warning(
            "the .env credential for %s is missing %s — adopting it anyway so the "
            "stages it can serve still run",
            domain,
            ",".join(str(s) for s in detail.get("missing_scopes", [])),
        )
    except AppError:
        # A credential that cannot even authenticate is not worth storing; the
        # caller reports "not connected" rather than the app carrying a dead row.
        log.warning("the .env credential for %s could not be verified", domain)
        return None

    now = utcnow()
    connection = connections.add(
        ShopifyConnection(
            workspace_id=workspace_id,
            shop_domain=domain,
            access_token_encrypted=crypto.encrypt(settings, token),
            status=status,
            store_name=profile.store_name if profile else None,
            plan_name=profile.plan_name if profile else None,
            currency=profile.currency if profile else None,
            token_scopes=",".join(granted) or None,
            connected_at=now,
            last_verified_at=now,
            connected_by=user_id,
        )
    )
    log.info(
        "adopted the .env credential for workspace=%s shop=%s status=%s — stored encrypted",
        workspace_id,
        connection.shop_domain,
        status,
    )
    return connection


def verify_env_credential(settings: Settings) -> ShopProfile:
    """Re-test the .env credential. There is no row to record a verdict on."""
    if not settings.env_shopify_credential_active:
        raise NotConnectedError
    return fetch_profile(
        settings,
        shop_domain=normalize_shop_domain(settings.shopify_store_url),
        token=settings.shopify_admin_api_token.strip(),
    )


def verify_stored_connection(
    db: Session, settings: Settings, *, workspace_id: int
) -> tuple[ShopifyConnection, ShopProfile]:
    """Re-test the credential already on file, and record the verdict.

    Writing the failure back is the point: it is what puts the red dot on
    Connection in the sidebar (§4) instead of the failure being invisible until
    someone opens this page.

    A missing scope and an expired token are recorded separately, for the same
    reason the sync records them separately — "token expired" sends the user to
    regenerate a credential that authenticates perfectly, when the fix is a
    scope grant in the Shopify admin.
    """
    connection = ShopifyConnectionRepository(db).get(workspace_id)
    if connection is None or connection.status == "disconnected":
        raise NotConnectedError

    token = crypto.decrypt(settings, connection.access_token_encrypted)
    try:
        profile = fetch_profile(settings, shop_domain=connection.shop_domain, token=token)
    except ShopifyScopeError as exc:
        connection.status = "missing_scopes"
        # The scopes it *does* have are on the error; recording them is what lets
        # the Store card show the user which one is absent.
        granted = [str(s) for s in (exc.detail or {}).get("granted_scopes", [])]
        if granted:
            connection.token_scopes = ",".join(granted)
        connection.last_verified_at = utcnow()
        db.commit()
        raise
    except ShopifyAuthError:
        connection.status = "token_expired"
        db.commit()
        raise

    connection.store_name = profile.store_name
    connection.plan_name = profile.plan_name
    connection.currency = profile.currency
    connection.token_scopes = ",".join(profile.scopes) or connection.token_scopes
    connection.status = "connected"
    connection.last_verified_at = utcnow()
    return connection, profile


def disconnect(db: Session, settings: Settings, *, workspace_id: int) -> ShopifyConnection:
    """Revoke locally: drop the token, keep the record of what was connected."""
    connection = ShopifyConnectionRepository(db).get(workspace_id)
    if connection is None or connection.status == "disconnected":
        # Distinguish "nothing connected" from "connected, but by a file this
        # endpoint cannot edit" — deleting nothing and reporting success would
        # leave the store still connected on the next page load.
        if settings.env_shopify_credential_active:
            raise EnvCredentialError
        raise NotConnectedError

    # Overwritten rather than left in place. A disconnected store that still
    # holds a usable token is a credential nobody is watching.
    connection.access_token_encrypted = ""
    connection.token_scopes = None
    connection.status = "disconnected"
    connection.disconnected_at = utcnow()

    log.info("shopify disconnected workspace=%s shop=%s", workspace_id, connection.shop_domain)
    return connection
