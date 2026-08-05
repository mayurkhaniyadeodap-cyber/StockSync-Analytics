"""Request and response bodies for inventory import."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RejectedRowPayload(BaseModel):
    row_number: int
    reason: str
    detail: str


class DuplicatePayload(BaseModel):
    sku: str
    rows: list[int]
    merged_quantity: int


class ImportBatchSummary(BaseModel):
    """One row of Import History (§8.8)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    method: str
    origin_filename: str
    status: str
    rows_read: int
    rows_imported: int
    rows_merged: int
    rows_flagged: int
    rows_rejected: int
    error_code: str | None
    error_detail: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class AnalysisSummaryPayload(BaseModel):
    """The analysis the import completed, returned with it.

    An import is not finished when the rows land — it is finished when those
    SKUs have been matched against Shopify sales. Returning that here is what
    lets the summary screen state the outcome instead of sending the user to the
    dashboard to find out.
    """

    skus_analyzed: int = 0
    skus_matched: int = 0
    skus_unmatched: int = 0
    shopify_sales: int = 0
    shopify_sales_pct: float = 0.0
    total_complaints: int = 0


class SyncAfterImportPayload(BaseModel):
    """Whether a Shopify sync was queued when the import landed.

    An import restates which SKUs matter; their sales are only as current as
    the last sync, so one is started automatically. The client follows it from
    the import screen, which is why the run id comes back here rather than the
    page having to go looking for it.
    """

    #: True when a run was queued. False is not a failure — see ``reason``.
    started: bool = False
    #: The run to poll, when one was queued.
    run_id: int | None = None
    #: Why no run was queued: ``not_connected`` when there is no Shopify store
    #: to fetch from, ``already_running`` when one was already in flight and
    #: will cover this import anyway. Null when one was.
    reason: str | None = None


class ImportResult(BaseModel):
    """The summary screen (§8.7), returned by the upload endpoint.

    Rejected rows and duplicate groups are returned but not stored: they are
    about *this* upload, and the per-row audit table is a later milestone.
    Capped so a badly formed 5,000-row file cannot return 5,000 messages.
    """

    batch: ImportBatchSummary
    items_created: int
    items_updated: int
    #: SKUs the previous dataset held that this file does not. An import
    #: states the whole dataset, so these are gone — reported because a
    #: number that drops from 1,641 to 309 should never be a surprise.
    items_removed: int = 0
    header_row_number: int
    detected_columns: dict[str, str]
    rejected: list[RejectedRowPayload]
    duplicates: list[DuplicatePayload]
    rejected_truncated: bool = False
    duplicates_truncated: bool = False

    #: The Shopify sync started for this import, if one was.
    sync: SyncAfterImportPayload = SyncAfterImportPayload()

    #: Which shape the sheet turned out to be: ``aggregated`` (one row per SKU,
    #: counts already totalled) or ``complaints`` (one row per complaint, grouped
    #: here). Returned so the summary can say what it did rather than leaving the
    #: user to infer it from the row counts.
    sheet_format: str = "aggregated"
    #: What the import worked out once Shopify sales were matched onto these
    #: SKUs. Returned with the upload so the summary screen can state the
    #: outcome rather than sending the user to the dashboard to find it.
    analysis: AnalysisSummaryPayload = AnalysisSummaryPayload()
    #: Reasons the mapping could not place, and how many rows carried each.
    #: Those rows still counted towards the totals; only the complaint breakdown
    #: missed them, and silence would look like clean data.
    unmapped_reasons: dict[str, int] = Field(default_factory=dict)
    #: Stable codes for things the reader noticed that are not failures — a Date
    #: column it recognised and then could not use, for instance. The client
    #: words them; a sentence written here is a sentence that goes stale where
    #: nobody looks.
    warnings: list[str] = Field(default_factory=list)


class GoogleSheetImportRequest(BaseModel):
    """Design doc §8.4 — the body of a Google Sheet import.

    Which URLs are sheets is decided in ``services.google_sheets``; the address
    and size guards that run before anything is parsed live in
    ``services.import_url``, which this fetch is built on.
    """

    url: str = Field(min_length=8, max_length=2048)


class LinkSheetRequest(BaseModel):
    """Link a sheet, and import it in the same step.

    Linking without importing would leave a row claiming a sheet is connected
    when nothing has ever proved the link is readable. The import *is* the
    proof, so a sheet that cannot be read is never recorded as linked.
    """

    url: str = Field(min_length=8, max_length=2048)
    #: Optional because the same upsert backs the Import page, which asks for a
    #: link and nothing else. A blank name becomes a derived label.
    name: str | None = Field(default=None, max_length=120)


class LinkedSheetPayload(BaseModel):
    """One row of Settings -> Google Sheets."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    last_synced_at: datetime | None
    last_status: str | None
    last_batch_id: int | None


class LinkedSheetList(BaseModel):
    items: list[LinkedSheetPayload]


class ImportHistoryPage(BaseModel):
    items: list[ImportBatchSummary]
    total: int
    limit: int
    offset: int


class InventorySummary(BaseModel):
    """Header figures for the import screen — real counts, never placeholders."""

    total_skus: int
    total_quantity: int
    last_imported_at: datetime | None


class InventoryItemPayload(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    product_name: str
    category: str | None
    price_paise: int | None
    quantity_on_hand: int
    last_imported_at: datetime


class InventoryPage(BaseModel):
    items: list[InventoryItemPayload]
    total: int
    limit: int
    offset: int
