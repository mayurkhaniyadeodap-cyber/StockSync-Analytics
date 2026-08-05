"""Request and response bodies for the Shopify connection."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConnectionRequest(BaseModel):
    """Step 1 and 2 of the connection wizard (§9.2)."""

    shop_url: str = Field(min_length=1, max_length=255)
    # Shopify Admin API tokens are `shpat_` + 32 hex characters, but the prefix
    # and length are Shopify's to change. Bounded rather than pattern-matched,
    # and the authoritative check is that Shopify accepts it.
    access_token: str = Field(min_length=8, max_length=512)


#: The three windows Settings offers. Bounded to a list rather than a range
#: because each value is a deliberate trade between how far back sales are
#: matched and how long a full sync takes, not a dial to be set to 47.
LOOKBACK_DAYS = (30, 60, 90)


class ConnectionSettingsUpdate(BaseModel):
    """Settings that change without re-proving the credential.

    Deliberately separate from ``ConnectionRequest``: changing how far back
    orders are read is not a reason to make someone paste their Admin API token
    again, and the token is not recoverable from the API to prefill.
    """

    order_lookback_days: int

    @field_validator("order_lookback_days")
    @classmethod
    def _known_window(cls, value: int) -> int:
        if value not in LOOKBACK_DAYS:
            raise ValueError(f"must be one of {', '.join(str(d) for d in LOOKBACK_DAYS)}")
        return value


class ShopProfilePayload(BaseModel):
    """§9.2 step 3: proof the connection works, before anything is saved."""

    shop_domain: str
    store_name: str | None
    plan_name: str | None
    currency: str | None
    scopes: list[str]


class ConnectionPayload(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Null for an environment-configured store: there is no row, and inventing
    # an id would make it look like something the connection endpoints can act
    # on. Always an integer when the connection came from the database.
    id: int | None = None
    shop_domain: str
    store_name: str | None
    plan_name: str | None
    currency: str | None
    token_scopes: str | None
    order_lookback_days: int
    status: str
    connected_at: datetime | None
    disconnected_at: datetime | None
    last_verified_at: datetime | None

    #: When the newest order in the *store* was placed, as Shopify last reported
    #: it, and when that was read. Already recorded by every sync and by
    #: /shopify/freshness — surfaced here so a screen can say "new orders are
    #: waiting" from data it already has, without spending a Shopify request on
    #: a page load.
    store_latest_order_at: datetime | None = None
    freshness_checked_at: datetime | None = None


class ConnectionState(BaseModel):
    """What the Shopify page renders.

    ``connection`` is null before a store has ever been connected, which is the
    empty state in §9.4. A disconnected store returns the row with
    ``status='disconnected'`` — the page shows the same empty state, but the
    record of what was connected survives.
    """

    connected: bool
    connection: ConnectionPayload | None
    # Where the credential came from. The page needs this to know whether
    # Disconnect can do anything: an environment-configured store is changed by
    # editing .env, not by an API call. Defaulted so existing clients that
    # ignore it keep working unchanged.
    source: Literal["database", "environment", "none"] = "none"


class TestConnectionResult(BaseModel):
    ok: bool
    profile: ShopProfilePayload


class SyncRunPayload(BaseModel):
    """One row of Sync history (design doc §9.1), and the progress poll target."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: str
    status: str
    stage: str
    orders_pct: int
    orders_synced: int
    line_items_synced: int
    result: str | None
    error_code: str | None
    error_detail: str | None
    retry_after_seconds: int | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    is_running: bool


class ActivityStepPayload(BaseModel):
    """One step of the automatic workflow, as Sync History shows it."""

    model_config = ConfigDict(from_attributes=True)

    #: A stable identifier — `import_started`, `recompute_failed`, and so on.
    #: The client words it; the server does not send display text.
    step: str
    #: `started`, `ok` or `failed`. `started` is a step with no outcome yet,
    #: which is what a log of a long job needs while it is still running.
    state: str
    detail: str | None
    at: datetime


class FreshnessPayload(BaseModel):
    """How far behind the live store the synced orders are.

    ``behind`` is None when Shopify could not be reached — "we do not know" is a
    different answer from "we are current", and the UI must be able to say so.
    """

    synced_through: datetime | None
    store_latest_order_at: datetime | None
    checked_at: datetime | None
    behind: bool | None
    behind_seconds: int | None
    behind_hours: float | None


class SyncHistoryPage(BaseModel):
    items: list[SyncRunPayload]
    total: int
    limit: int
    offset: int


class SyncState(BaseModel):
    """What the Shopify page polls while a sync is in flight.

    ``running`` is derived rather than inferred by the client from ``stage``,
    so there is one definition of in-flight and the UI cannot disagree with the
    server about whether to keep polling.
    """

    running: bool
    run: SyncRunPayload | None
    last_synced_at: datetime | None


class SalesSummary(BaseModel):
    """What the last sync left behind.

    Orders and line items only. The product and variant counts went with the
    catalogue — Shopify is now a sales source, not a catalogue source.
    """

    orders: int
    line_items: int
    #: Distinct SKUs that sold, which is what a sheet SKU can match against.
    skus_with_sales: int
    last_synced_at: datetime | None
