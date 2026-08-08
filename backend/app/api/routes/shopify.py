"""Shopify connection endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, SettingsDep, enforce_rate_limit
from app.config import Settings
from app.core.errors import AppError
from app.models import ActivityEvent
from app.repositories import (
    OrderRepository,
    ShopifyConnectionRepository,
    SyncRunRepository,
)
from app.schemas.shopify import (
    ActivityStepPayload,
    ConnectionPayload,
    ConnectionRequest,
    ConnectionSettingsUpdate,
    ConnectionState,
    FreshnessPayload,
    SalesSummary,
    ShopProfilePayload,
    SyncHistoryPage,
    SyncRunPayload,
    SyncState,
    TestConnectionResult,
)
from app.services import shopify as shopify_service
from app.services import sync as sync_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["shopify"], prefix="/shopify")


def _profile_payload(profile: shopify_service.ShopProfile) -> ShopProfilePayload:
    return ShopProfilePayload(
        shop_domain=profile.shop_domain,
        store_name=profile.store_name,
        plan_name=profile.plan_name,
        currency=profile.currency,
        scopes=profile.scopes,
    )


def _state(connection: object | None) -> ConnectionState:
    if connection is None:
        return ConnectionState(connected=False, connection=None, source="none")
    payload = ConnectionPayload.model_validate(connection)
    return ConnectionState(
        connected=payload.status == "connected",
        connection=payload,
        source="database" if payload.status == "connected" else "none",
    )


def _env_state(settings: Settings) -> ConnectionState:
    """The .env-configured store, rendered as a connection.

    Only the store URL and the fields Shopify would report are exposed. The
    token is not here, in any form — this endpoint has never returned it and
    reading it from a file rather than a database row does not change that.
    """
    return ConnectionState(
        connected=True,
        source="environment",
        connection=ConnectionPayload(
            id=None,
            shop_domain=shopify_service.normalize_shop_domain(settings.shopify_store_url),
            store_name=None,
            plan_name=None,
            currency=None,
            token_scopes=None,
            order_lookback_days=90,
            status="connected",
            connected_at=None,
            disconnected_at=None,
            last_verified_at=None,
        ),
    )


@router.get("/connection", response_model=ConnectionState, summary="Current connection")
def get_connection(user: CurrentUser, db: DbDep, settings: SettingsDep) -> ConnectionState:
    """Design doc §9.1/§9.4. Never returns the token, encrypted or otherwise.

    A connection stored here wins over ``.env`` — see
    ``shopify_service.resolve_credential`` for why that ordering matters.
    """
    connection = ShopifyConnectionRepository(db).get(user.workspace_id)
    if connection is not None and connection.status == "connected":
        return _state(connection)

    if settings.env_shopify_credential_active:
        return _env_state(settings)

    return _state(connection)


@router.post(
    "/connection/test",
    response_model=TestConnectionResult,
    summary="Test a store URL and token without saving",
)
def test_connection(
    payload: ConnectionRequest,
    user: CurrentUser,
    settings: SettingsDep,
) -> TestConnectionResult:
    """The Test Connection button — §9.2 step 3.

    Writes nothing. The user can prove a credential works before committing it,
    which is the whole point of the third wizard step.
    """
    profile = shopify_service.test_connection(
        settings, shop_url=payload.shop_url, token=payload.access_token
    )
    log.info("shopify test succeeded workspace=%s shop=%s", user.workspace_id, profile.shop_domain)
    return TestConnectionResult(ok=True, profile=_profile_payload(profile))


@router.post("/connection", response_model=ConnectionState, summary="Save credentials")
def save_connection(
    payload: ConnectionRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
) -> ConnectionState:
    """Validate against Shopify, then store the token encrypted at rest."""
    connection, _profile = shopify_service.save_connection(
        db,
        settings,
        workspace_id=user.workspace_id,
        user=user,
        shop_url=payload.shop_url,
        token=payload.access_token,
    )
    db.commit()
    db.refresh(connection)
    return _state(connection)


class ConnectionNotStoredError(AppError):
    code = "connection_not_stored"
    status_code = 404
    message = "There's no stored Shopify connection to change."
    next_step = "Connect a store first, then these settings become editable."


class EnvConnectionReadOnlyError(AppError):
    code = "env_connection_read_only"
    status_code = 409
    message = "This store is configured in the server environment, so it can't be changed here."
    next_step = "Update the deployment's configuration, or connect a store from this page instead."


@router.patch(
    "/connection",
    response_model=ConnectionState,
    summary="Update connection settings",
)
def update_connection(
    payload: ConnectionSettingsUpdate,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
) -> ConnectionState:
    """Change what the connection *does*, without touching what proves it.

    Only settings live here. The credential has its own endpoint because saving
    one has to be validated against Shopify first, and a window change does not.
    """
    connection = ShopifyConnectionRepository(db).get(user.workspace_id)
    if connection is None or connection.status != "connected":
        # A store named only in .env has no row to write to, and saying "not
        # connected" about a store the page shows as connected would be a lie.
        if settings.env_shopify_credential_active:
            raise EnvConnectionReadOnlyError
        raise ConnectionNotStoredError

    connection.order_lookback_days = payload.order_lookback_days
    db.commit()
    db.refresh(connection)
    log.info(
        "shopify lookback set workspace=%s days=%s",
        user.workspace_id,
        payload.order_lookback_days,
    )
    return _state(connection)


@router.post(
    "/connection/verify",
    response_model=ConnectionState,
    summary="Re-test the stored credential",
)
def verify_connection(user: CurrentUser, db: DbDep, settings: SettingsDep) -> ConnectionState:
    stored = ShopifyConnectionRepository(db).get(user.workspace_id)
    if (stored is None or stored.status != "connected") and settings.env_shopify_credential_active:
        # Nothing to record a verdict against, so this just proves the .env
        # pair still works.
        shopify_service.verify_env_credential(settings)
        return _env_state(settings)

    connection, _profile = shopify_service.verify_stored_connection(
        db, settings, workspace_id=user.workspace_id
    )
    db.commit()
    db.refresh(connection)
    return _state(connection)


@router.delete("/connection", response_model=ConnectionState, summary="Disconnect the store")
def disconnect(user: CurrentUser, db: DbDep, settings: SettingsDep) -> ConnectionState:
    connection = shopify_service.disconnect(db, settings, workspace_id=user.workspace_id)
    db.commit()
    db.refresh(connection)
    # A stored connection can be removed while .env still names a store, and
    # the page must then show the .env one rather than the empty state.
    if settings.env_shopify_credential_active:
        return _env_state(settings)
    return _state(connection)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


class SyncNotFoundError(AppError):
    code = "sync_not_found"
    status_code = 404
    message = "That sync doesn't exist."
    next_step = "Go back to Sync history and pick one from the list."


def _sync_state(db: DbDep, workspace_id: int) -> SyncState:
    # Recovery cannot depend on a restart or on someone pressing Sync now.
    # Start-up reclaim misses a run killed less than STALE_RUN_AFTER before the
    # process came back — too fresh to reclaim then, and nothing looks again —
    # so it stays `running` forever, blocks every future sync, and the UI polls
    # a progress bar that will never move.
    #
    # This is the poll, so a dead run is cleared within one client tick. It is
    # a write on a read path, which is why the reclaim is written to select
    # first and commit only when there is genuinely something stale: the common
    # case is one indexed SELECT returning nothing.
    sync_service.reclaim_interrupted_runs(db)

    runs = SyncRunRepository(db)
    current = runs.active(workspace_id) or runs.latest(workspace_id)
    return SyncState(
        running=bool(current and current.is_running),
        run=SyncRunPayload.model_validate(current) if current else None,
        last_synced_at=runs.last_success_at(workspace_id),
    )


@router.post(
    "/sync",
    response_model=SyncState,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a sync",
)
def start_sync(user: CurrentUser, db: DbDep, settings: SettingsDep) -> SyncState:
    """Queue a pull of Shopify orders.

    Orders only. The product and variant pull this used to describe was removed
    along with the catalogue — Shopify is a sales source here, and nothing reads
    a product record.

    Returns 202 with the queued run rather than waiting: a full sync takes
    seconds to minutes, and holding the request open for it would time out on
    any proxy in front of the API. The client polls ``GET /shopify/sync``.
    """
    # Distinct from "a sync is already running", which the service raises: that
    # one says *wait for this*, this one says *you have started several*. A
    # sync can legitimately finish and be restarted, which is the loop this
    # bounds.
    enforce_rate_limit(settings, user, operation="sync", what="sync")
    sync_service.start_sync(
        db,
        settings,
        workspace_id=user.workspace_id,
        user_id=user.id,
    )
    # The job runs on its own session and may already have advanced the row.
    # This session was created with expire_on_commit=False, so without an
    # explicit expire it would answer from its own stale copy and report the
    # run as still queued.
    db.expire_all()
    return _sync_state(db, user.workspace_id)


@router.post(
    "/sync/retry",
    response_model=SyncState,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry whatever the last sync failed at",
)
def retry_sync(user: CurrentUser, db: DbDep, settings: SettingsDep) -> SyncState:
    """Repeat only the stage that failed.

    A Shopify pull that stopped resumes from its cursor. A pull that worked
    with a recompute that did not skips Shopify entirely — those orders are
    already here. Neither case needs the sheet uploading again.
    """
    sync_service.retry_sync(
        db,
        settings,
        workspace_id=user.workspace_id,
        user_id=user.id,
    )
    db.expire_all()
    return _sync_state(db, user.workspace_id)


@router.get("/sync", response_model=SyncState, summary="Current or most recent sync")
def sync_state(user: CurrentUser, db: DbDep) -> SyncState:
    """The progress poll target while a sync runs (design doc §9.3)."""
    return _sync_state(db, user.workspace_id)


@router.get("/syncs", response_model=SyncHistoryPage, summary="Sync history")
def list_syncs(
    user: CurrentUser,
    db: DbDep,
    result: str | None = Query(default=None, pattern="^(success|partial|failed)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SyncHistoryPage:
    """Design doc §9.1, newest first."""
    runs = SyncRunRepository(db)
    return SyncHistoryPage(
        items=[
            SyncRunPayload.model_validate(r)
            for r in runs.list(user.workspace_id, result=result, limit=limit, offset=offset)
        ],
        total=runs.count(user.workspace_id, result=result),
        limit=limit,
        offset=offset,
    )


@router.get("/syncs/{run_id}", response_model=SyncRunPayload, summary="One sync")
def get_sync(run_id: int, user: CurrentUser, db: DbDep) -> SyncRunPayload:
    run = SyncRunRepository(db).get(user.workspace_id, run_id)
    if run is None:
        raise SyncNotFoundError
    return SyncRunPayload.model_validate(run)


@router.get(
    "/syncs/{run_id}/steps",
    response_model=list[ActivityStepPayload],
    summary="Every step of one run",
)
def sync_steps(run_id: int, user: CurrentUser, db: DbDep) -> list[ActivityStepPayload]:
    """The account of how a run reached its result.

    Includes the import that started it, when one did: the two are one
    workflow, and reading them apart is how a partial run looks like a
    mystery rather than a recompute that failed.
    """
    if SyncRunRepository(db).get(user.workspace_id, run_id) is None:
        raise SyncNotFoundError
    rows = db.scalars(
        select(ActivityEvent)
        .where(
            ActivityEvent.workspace_id == user.workspace_id,
            ActivityEvent.run_id == run_id,
        )
        .order_by(ActivityEvent.at, ActivityEvent.id)
    )
    return [ActivityStepPayload.model_validate(row) for row in rows]


@router.get("/sales/summary", response_model=SalesSummary, summary="What the sync pulled")
def sales_summary(user: CurrentUser, db: DbDep) -> SalesSummary:
    orders = OrderRepository(db)
    return SalesSummary(
        orders=orders.count_orders(user.workspace_id),
        line_items=orders.count_line_items(user.workspace_id),
        skus_with_sales=orders.count_skus_with_sales(user.workspace_id),
        last_synced_at=SyncRunRepository(db).last_success_at(user.workspace_id),
    )


@router.get("/freshness", response_model=FreshnessPayload, summary="How current the orders are")
def freshness(user: CurrentUser, db: DbDep, settings: SettingsDep) -> FreshnessPayload:
    """Compare the newest synced order against the newest order in the store.

    Costs one Shopify request, so it is a deliberate call rather than something
    every dashboard read does. The answer is recorded on the connection, which
    is what lets other screens report the gap without paying for it again.
    """
    computed = sync_service.check_freshness(db, settings, workspace_id=user.workspace_id)
    return FreshnessPayload(
        synced_through=computed.synced_through,
        store_latest_order_at=computed.store_latest,
        checked_at=computed.checked_at,
        behind=computed.behind,
        behind_seconds=computed.behind_seconds,
        behind_hours=computed.behind_hours,
    )
