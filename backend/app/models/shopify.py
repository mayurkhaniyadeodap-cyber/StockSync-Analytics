"""Shopify: the store connection, the sync log, and the orders a sync pulls.

Plan §2.3. The connection arrived in M2; the sync tables land here.

**Shopify's ids are the durable identity.** Every synced row carries the
``shopify_*_id`` bigint it came from and is upserted on it, so re-syncing updates
in place rather than accumulating copies. Our own surrogate ``id`` is for local
foreign keys only.

The product and variant tables this module used to define are gone, along with
the soft-delete rule that existed for them: orders carry ``sku_at_sale``, so
sales attribute to an uploaded SKU without the catalogue. What remains is the
connection, the sync log, and orders with their line items.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin, UtcDateTime

# 'token_expired' is set when Shopify rejects a token that previously worked;
# §4 uses it to put the red dot on Connection in the sidebar.
# 'missing_scopes' is distinct from 'token_expired' on purpose: a token can
# authenticate perfectly and still be unable to read products. Collapsing the
# two sends the user to regenerate a working token instead of to the scope
# grant that actually fixes it.
CONNECTION_STATUSES = ("connected", "missing_scopes", "token_expired", "disconnected")

# In-flight stages drive the progress bar in design doc §9.3. There is one:
# orders carry `sku_at_sale`, so sales attribution never needed the catalogue,
# and dropping it removed the read_products scope requirement with it.
SYNC_STAGES = ("queued", "orders", "done")
SYNC_STATUSES = ("queued", "running", "finished")
# 'partial' is not a synonym for failure: some pages landed. It is what makes
# the cursors below meaningful.
SYNC_RESULTS = ("success", "partial", "failed")
#: "import" is the ordinary case now: every successful upload starts a sync, so
#: the sales beside the SKUs it just established are current without anyone
#: pressing anything. "manual" is the Shopify page's button, kept as the way
#: back after a failure and for a refresh between imports.
SYNC_TRIGGERS = ("manual", "scheduled", "import", "retry")


class ShopifyConnection(IdMixin, TimestampMixin, Base):
    """One workspace's link to one Shopify store.

    One row per workspace, enforced by the unique constraint: the product
    reconciles *a* store against *an* inventory sheet, and a second connection
    would make "which store did this SKU sell in" ambiguous. Disconnecting
    keeps the row (status becomes ``disconnected``) so the history of what was
    connected survives.
    """

    __tablename__ = "shopify_connections"
    __table_args__ = (UniqueConstraint("workspace_id", name="workspace_shopify_connection"),)

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Canonical "example.myshopify.com" — no scheme, no path, lowercased.
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fernet ciphertext, never the token. Text rather than String(n) because
    # ciphertext length depends on the plaintext and Fernet's framing.
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Reported by Shopify at connect time, so the UI can show what the token can
    # actually do instead of what we hoped it could.
    token_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)

    store_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # A real setting in the prototype (30/90/180), not a constant.
    order_lookback_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="connected")

    connected_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # When the newest order in the *store* was placed, as Shopify last reported
    # it. Stored rather than fetched per page load: it is the only way to know
    # the database is behind the store, and a live call on every dashboard read
    # would spend a Shopify request to render a caption.
    store_latest_order_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: When ``store_latest_order_at`` was last read, so a stale answer can say so.
    freshness_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    connected_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_live(self) -> bool:
        return self.status == "connected"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ShopifyConnection {self.shop_domain!r} {self.status!r}>"


class SyncRun(IdMixin, TimestampMixin, Base):
    """One pull from Shopify, successful or not.

    ``cursor_orders`` is what makes the prototype's footer promise true —
    *"Partial syncs re-fetch only the missing pages on the next run."* Without
    it "partial" is a label with no mechanism behind it: the next run would
    start from page one and redo work that already landed.

    There is one stage and one cursor. The product columns this table used to
    carry (``products_pct``, ``products_synced``, ``variants_synced``,
    ``cursor_products``) were dropped once the catalogue was: nothing wrote them
    after that refactor, so every row reported 0 products synced and the UI drew
    a progress bar that could only ever sit at 0%.
    """

    __tablename__ = "sync_runs"
    __table_args__ = (
        Index("ix_sync_runs_workspace_started", "workspace_id", "started_at"),
        # **One live run per workspace, enforced by the database.**
        #
        # `start_sync` reads `runs.active()` and inserts if it finds nothing, and
        # nothing between the two is atomic. Under ten concurrent callers that
        # let two runs through — measured, not theorised — which then fetched the
        # same window twice and rebuilt the rollup twice. The data survived it
        # (the order upserts are idempotent), but the work was done twice.
        #
        # A partial unique index makes the check-then-insert atomic at the only
        # layer where it can be. SQLite and PostgreSQL both support the form.
        Index(
            "uq_sync_runs_one_live_per_workspace",
            "workspace_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("shopify_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )

    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")

    # 0-100 for the one stage there is.
    orders_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    orders_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_items_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Where to resume from. Null once the stage completes cleanly.
    cursor_orders: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    triggered_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    @property
    def is_running(self) -> bool:
        return self.status in ("queued", "running")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SyncRun {self.id} {self.status!r} {self.result!r}>"


class Order(IdMixin, TimestampMixin, Base):
    """A Shopify order header.

    ``cancelled_at`` and ``financial_status`` are stored rather than filtered
    at ingest, because "exclude cancelled and refunded from sales" is a query
    concern that different screens answer differently — and discarding the rows
    would make the decision unrevisitable.
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("workspace_id", "shopify_order_id", name="workspace_order"),
        Index("ix_orders_workspace_processed", "workspace_id", "processed_at"),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("shopify_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )

    shopify_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at_shopify: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    financial_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fulfillment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    total_price_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    line_items: Mapped[list[OrderLineItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Order {self.shopify_order_id} {self.financial_status!r}>"


class OrderLineItem(IdMixin, Base):
    """One line of an order.

    ``sku_at_sale`` is not optional. Shopify denormalises the SKU onto the line
    at purchase time, and a variant can be renamed or deleted afterwards —
    storing the historical string is what lets a sale still reconcile to
    inventory after the variant is gone.
    """

    __tablename__ = "order_line_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "shopify_line_item_id", name="workspace_line_item"),
        Index("ix_order_line_items_sku", "workspace_id", "sku_normalized"),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )

    shopify_line_item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Nullable: a line can reference a variant that was deleted before we ever
    # synced it, and refusing to store the sale would lose real revenue.
    shopify_variant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    sku_at_sale: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sku_normalized: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_discount_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order: Mapped[Order] = relationship(back_populates="line_items")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OrderLineItem {self.shopify_line_item_id} sku={self.sku_at_sale!r}>"


# ---------------------------------------------------------------------------
# "counts as a sale" — one definition
# ---------------------------------------------------------------------------

#: Orders in these states are not sales (design doc §7, plan §2.3).
EXCLUDED_FINANCIAL_STATUSES: tuple[str, ...] = ("refunded", "voided")


def sale_filters() -> list[ColumnElement[bool]]:
    """**The one definition of "counts as a sale"**, as SQL.

    Four conditions, and every one of them earns its place:

    * not cancelled, and not refunded or voided — those are returns, not sales;
    * a non-blank SKU, because a line that cannot be attributed to a SKU cannot
      be reconciled against the sheet and would inflate the store total the
      Shopify Sales % card divides by;
    * a ``processed_at``, because the rollup buckets by that date and a null
      would have no day to belong to.

    Lived here rather than in ``services.metrics`` because it stopped being one
    definition the moment a second copy appeared: ``Order.counts_as_sale`` used
    to assert the first two conditions only, so the model and the rollup
    disagreed about 58 blank-SKU units. Anything that needs the rule now imports
    it, and :func:`counts_as_sale` below is the same rule for a loaded row.
    """
    return [
        Order.cancelled_at.is_(None),
        func.coalesce(Order.financial_status, "").notin_(EXCLUDED_FINANCIAL_STATUSES),
        OrderLineItem.sku_normalized != "",
        Order.processed_at.is_not(None),
    ]


def counts_as_sale(order: Order, line: OrderLineItem) -> bool:
    """The same rule as :func:`sale_filters`, for a row already in memory.

    Takes the line as well as the order because two of the four conditions are
    the line's. A version that took only the order could not answer the
    question, which is exactly how the old model property came to be wrong.
    """
    return (
        order.cancelled_at is None
        and (order.financial_status or "") not in EXCLUDED_FINANCIAL_STATUSES
        and line.sku_normalized != ""
        and order.processed_at is not None
    )
