/**
 * API response types.
 *
 * Hand-written for M1. Once the schema settles these should be generated from
 * the FastAPI OpenAPI document so the two sides cannot drift.
 */

/** The error shape every failed response uses — see backend app/core/errors.py. */
export interface ApiError {
  /** Stable machine-readable identifier, e.g. "shopify_rate_limited". */
  code: string;
  /** What happened. States the fact; does not apologise. */
  message: string;
  /** What to do next. Design doc §16 requires this on every error. */
  next: string;
  detail?: Record<string, unknown>;
}

export interface ErrorEnvelope {
  error: ApiError;
}

export type HealthStatus = 'ok' | 'degraded';

export interface DatabaseHealth {
  status: 'ok' | 'unreachable';
  latency_ms: number | null;
  reason: string | null;
}

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  environment: 'development' | 'test' | 'production';
  database: DatabaseHealth;
}

export type Theme = 'light' | 'dark';
export type TableDensity = 'comfortable' | 'compact';

export interface Preferences {
  theme: Theme;
  table_density: TableDensity;
  alert_on_stockout: boolean;
}

/**
 * The Display panel's patch body.
 *
 * `low_stock_threshold` rides along because that is the panel it belongs to,
 * but unlike the three above it is workspace-scoped — it moves the line
 * everyone's Low stock figure is drawn at, which is why the response carries a
 * whole user rather than just preferences.
 */
export type PreferencesUpdate = Partial<Preferences> & { low_stock_threshold?: number };

/** The editable half of Profile. Email and role are shown but set elsewhere. */
export interface ProfileUpdate {
  full_name?: string;
  timezone?: string;
}

export interface WorkspaceSummary {
  id: number;
  name: string;
  slug: string;
  timezone: string;
  currency: string;
  low_stock_threshold: number;
}

export interface CurrentUser {
  id: number;
  email: string;
  full_name: string;
  role: string;
  timezone: string;
  initials: string;
  workspace: WorkspaceSummary;
  preferences: Preferences;
  /**
   * ISO timestamp at which the access cookie stops being accepted, so the
   * session can be renewed before a request fails rather than after. Null on
   * the profile and preferences patches, which neither issue a token nor read
   * one — a null means "fall back to recovering from the 401".
   *
   * Not a credential: the token stays in the httpOnly cookie.
   */
  access_expires_at: string | null;
}

/* ------------------------------------------------------------------ M2 */

/** Terminal statuses map to the three badges in design doc §8.8. */
export type ImportStatus =
  'pending' | 'reading' | 'validating' | 'saving' | 'complete' | 'partial' | 'failed';

export interface ImportBatchSummary {
  id: number;
  method: string;
  origin_filename: string;
  status: ImportStatus;
  rows_read: number;
  rows_imported: number;
  rows_merged: number;
  rows_flagged: number;
  rows_rejected: number;
  error_code: string | null;
  error_detail: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface RejectedRow {
  row_number: number;
  reason: string;
  detail: string;
}

export interface DuplicateGroup {
  sku: string;
  rows: number[];
  merged_quantity: number;
}

/** What the import worked out once Shopify sales were matched onto it. */
export interface AnalysisSummary {
  skus_analyzed: number;
  skus_matched: number;
  skus_unmatched: number;
  shopify_sales: number;
  shopify_sales_pct: number;
  total_complaints: number;
}

/** Whether a Shopify sync was queued when an import landed. */
export interface SyncAfterImport {
  started: boolean;
  run_id: number | null;
  /** `not_connected` or `already_running` when no run was queued. */
  reason: string | null;
}

export interface ImportResult {
  batch: ImportBatchSummary;
  items_created: number;
  items_updated: number;
  /**
   * SKUs the previous dataset held that this file does not. An import
   * states the whole dataset, so these are gone — surfaced because a SKU
   * count dropping from 1,641 to 309 should never be a surprise.
   */
  items_removed: number;
  header_row_number: number;
  detected_columns: Record<string, string>;
  rejected: RejectedRow[];
  duplicates: DuplicateGroup[];
  rejected_truncated: boolean;
  duplicates_truncated: boolean;
  /** The sync started for this import. Sales update without a button. */
  sync: SyncAfterImport;
  /**
   * Which shape the sheet turned out to be: `aggregated` (one row per SKU) or
   * `complaints` (one row per complaint, grouped by the server).
   */
  sheet_format: 'aggregated' | 'complaints';
  analysis: AnalysisSummary;
  /**
   * Reasons the server could not map to a complaint column, and how many rows
   * carried each. Those rows still counted towards the totals.
   */
  unmapped_reasons: Record<string, number>;
  /**
   * Stable codes for things the reader noticed that are not failures. Worded by
   * `IMPORT_WARNINGS` in the client, so the sentence lives next to the screen
   * that shows it rather than on a server that never renders it.
   *
   * Optional because a cached bundle can outlive a deploy in either direction,
   * and a missing field must not take the summary screen down over a warning.
   */
  warnings?: string[];
}

export interface ImportHistoryPage {
  items: ImportBatchSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface InventorySummary {
  total_skus: number;
  total_quantity: number;
  last_imported_at: string | null;
}

/** Mirrors CONNECTION_STATUSES in backend/app/models/shopify.py. */
export type ConnectionStatus =
  'connected' | 'missing_scopes' | 'token_expired' | 'disconnected';

/** Where the credential came from. `environment` means .env, not the database. */
export type ConnectionSource = 'database' | 'environment' | 'none';

export interface ShopifyConnection {
  /** Null for an environment-configured store — there is no row to act on. */
  id: number | null;
  shop_domain: string;
  store_name: string | null;
  plan_name: string | null;
  currency: string | null;
  token_scopes: string | null;
  order_lookback_days: number;
  status: ConnectionStatus;
  connected_at: string | null;
  disconnected_at: string | null;
  last_verified_at: string | null;
  /**
   * The newest order in the store as Shopify last reported it, and when that
   * was read. Recorded by every sync, so a screen can tell that new orders are
   * waiting without spending a live Shopify request on a page load.
   */
  store_latest_order_at: string | null;
  freshness_checked_at: string | null;
}

export interface ConnectionState {
  connected: boolean;
  connection: ShopifyConnection | null;
  source: ConnectionSource;
}

export interface ShopProfile {
  shop_domain: string;
  store_name: string | null;
  plan_name: string | null;
  currency: string | null;
  scopes: string[];
}

export interface TestConnectionResult {
  ok: boolean;
  profile: ShopProfile;
}

/* ------------------------------------------------------------------ M3 */

export type SyncResult = 'success' | 'partial' | 'failed';
export type SyncStage = 'queued' | 'orders' | 'done';

export interface SyncRun {
  id: number;
  trigger: string;
  status: string;
  stage: SyncStage;
  orders_pct: number;
  orders_synced: number;
  line_items_synced: number;
  result: SyncResult | null;
  error_code: string | null;
  error_detail: string | null;
  retry_after_seconds: number | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  is_running: boolean;
}

/** One step of the automatic workflow, from `/shopify/syncs/{id}/steps`. */
export interface SyncStep {
  /** A stable identifier — the client words it. */
  step: string;
  /** `started`, `ok` or `failed`. */
  state: string;
  detail: string | null;
  at: string;
}

export interface SyncState {
  running: boolean;
  run: SyncRun | null;
  last_synced_at: string | null;
}

/**
 * How far behind the live Shopify store the synced orders are.
 *
 * `behind` is `null` when Shopify could not be reached — "we do not know" is a
 * different answer from "we are current", and the UI has to be able to say so.
 */
export interface Freshness {
  synced_through: string | null;
  store_latest_order_at: string | null;
  checked_at: string | null;
  behind: boolean | null;
  behind_seconds: number | null;
  behind_hours: number | null;
}

export interface SyncHistoryPage {
  items: SyncRun[];
  total: number;
  limit: number;
  offset: number;
}

/** One linked Google Sheet — Settings, and the source of a re-runnable import. */
export interface LinkedSheet {
  id: number;
  name: string;
  url: string;
  last_synced_at: string | null;
  last_status: string | null;
  last_batch_id: number | null;
}

export interface LinkedSheetList {
  items: LinkedSheet[];
}

/** The windows Settings offers, and the server accepts. */
export const LOOKBACK_DAYS = [30, 60, 90] as const;

export interface SalesSummary {
  orders: number;
  line_items: number;
  /** Distinct SKUs that sold — the set a sheet SKU can match against. */
  skus_with_sales: number;
  last_synced_at: string | null;
}

// ---------------------------------------------------------------------------
// M5 — analytics
// ---------------------------------------------------------------------------

/** Money crosses the wire in paise (plan §4.5); the client formats. */
/** The six dashboard cards. Four from the sheet, two from Shopify. */
export interface Kpis {
  total_skus: number;
  total_quantity: number;
  total_orders: number;
  total_complaints: number;
  shopify_sales: number;
  /** Share of all Shopify units belonging to a SKU in the sheet. 0–100. */
  shopify_sales_pct: number;
  /** The denominator behind the percentage — every unit the store sold. */
  shopify_sales_all: number;
  revenue_paise: number;
  low_stock: number;
  low_stock_threshold: number;
  days: number;
  stale: boolean;
  /** True while a sync is queued or running. Never true at the same time as
   *  `stale`: figures being rebuilt are not figures that are behind. */
  syncing: boolean;
  last_computed_at: string | null;
}

export interface TrendPoint {
  day: string;
  units: number;
  revenue_paise: number;
}

export interface Trend {
  points: TrendPoint[];
  previous: TrendPoint[];
  days: number;
}

export type StockStatus = 'in' | 'low' | 'out';

/** One complaint category. The server sends the set with every page. */
export interface ComplaintColumn {
  field: string;
  header: string;
}

/**
 * Whether the complaint figures on a payload followed the selected range.
 *
 * The number alone cannot say: "no complaints this month" and "complaints we
 * cannot place in a month" look identical on screen.
 *
 * Counts, not copy. The sentence is built in `ComplaintScopeNote` from these
 * three, where the numbers can be grouped and made to agree grammatically. The
 * server used to send a `note` as well; nothing rendered it and it went stale.
 */
export interface ComplaintScope {
  filtered_by_date: boolean;
  dated_skus: number;
  undated_skus: number;
  undated_complaints: number;
}

export interface SkuRow {
  sku: string;
  sku_normalized: string;
  quantity: number;
  shopify_sales: number;
  /** A share of what the imported SKUs sold, so the column sums to 100%. Not
   *  the KPI card's percentage, which divides by the whole store. */
  shopify_sales_pct: number;
  total_orders: number;
  total_qty: number;
  total_count: number;
  /** Keyed by the same `field` values complaint_columns carries. */
  complaints: Record<string, number>;
  total_complaints: number;
  stock_status: StockStatus;
}

export interface SkuTablePage {
  complaint_scope: ComplaintScope;
  rows: SkuRow[];
  complaint_columns: ComplaintColumn[];
  total: number;
  limit: number;
  offset: number;
  days: number;
}

export interface RebuildResult {
  rows_written: number;
  days_covered: number;
  duration_ms: number;
}

export interface AnalyticsOverview {
  kpis: Kpis;
  trend: Trend;
  has_data: boolean;
}

// ---------------------------------------------------------------------------
// M6 — reports
// ---------------------------------------------------------------------------

export type ReportKind =
  'inventory' | 'sales' | 'sku_performance' | 'dashboard' | 'sku_matching';
export type ReportFormat = 'csv' | 'xlsx' | 'pdf';
export type ReportStatus = 'preparing' | 'ready' | 'failed';

export interface ReportColumn {
  header: string;
  /** The server decides which columns are figures, so the client needn't. */
  align: 'left' | 'right';
}

export interface ReportPreview {
  title: string;
  subtitle: string;
  columns: ReportColumn[];
  rows: string[][];
  /** More rows exist than the preview shows. */
  truncated: boolean;
}

/** One row of the Export Centre. Never carries the file itself. */
export interface Report {
  id: number;
  kind: ReportKind;
  fmt: ReportFormat;
  status: ReportStatus;
  filename: string;
  range_days: number | null;
  range_label: string;
  /** Set when the export was deliberately limited to the top rows; null = all. */
  row_limit?: number | null;
  row_count: number;
  size_bytes: number;
  error_code: string | null;
  error_detail: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface ReportHistoryPage {
  items: Report[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Analytics page (business intelligence). Separate from the dashboard's Kpis /
// SkuTablePage above: the two pages answer different questions and are free to
// diverge.
// ---------------------------------------------------------------------------

/** A SKU's automatic verdict, worst to best. */
export type SkuStatus = 'critical' | 'attention' | 'good' | 'excellent';

export interface InsightKpis {
  total_skus: number;
  total_qty: number;
  shopify_sales: number;
  shopify_sales_pct: number;
  total_orders: number;
  total_complaints: number;
  /** Units sold per SKU carried. */
  avg_sales_per_sku: number;
  shopify_sales_all: number;
}

export interface RankedSku {
  rank: number;
  sku: string;
  sku_normalized: string;
  shopify_sales: number;
  shopify_sales_pct: number;
  total_complaints: number;
  total_qty: number;
  total_orders: number;
}

/** One bar or slice. `field_name` is a complaint attribute or a SKU. */
export interface NamedCount {
  field_name: string;
  label: string;
  count: number;
  share_pct: number;
}

export interface SalesAnalytics {
  shopify_sales: number;
  shopify_sales_pct: number;
  /** Null when nothing sold — an absent finding, not a zero one. */
  highest: RankedSku | null;
  lowest: RankedSku | null;
  top: RankedSku[];
  distribution: NamedCount[];
}

export interface ComplaintAnalytics {
  total_complaints: number;
  most_complained: RankedSku | null;
  categories: NamedCount[];
  top_skus: RankedSku[];
  skus_with_complaints: number;
}

export interface Rankings {
  top_selling: RankedSku[];
  lowest_selling: RankedSku[];
  highest_complaint: RankedSku[];
}

export interface InventoryInsights {
  high_stock_low_sales: RankedSku[];
  low_stock_high_sales: RankedSku[];
  zero_sales: RankedSku[];
  most_complaints: RankedSku[];
  /** The cuts the lists were made at, so the UI can state them. */
  median_qty: number;
  median_sales: number;
  zero_sales_total: number;
}

export interface QuickInsight {
  key: string;
  /** Names an icon the client already has. */
  icon: string;
  title: string;
  sku: string | null;
  value: string;
  note: string;
}

export interface AnalyticsInsights {
  kpis: InsightKpis;
  sales: SalesAnalytics;
  complaints: ComplaintAnalytics;
  rankings: Rankings;
  inventory: InventoryInsights;
  quick: QuickInsight[];
  trend: Trend;
  complaint_columns: ComplaintColumn[];
  complaint_scope: ComplaintScope;
  days: number;
  has_data: boolean;
  stale: boolean;
  /** True while a sync is queued or running. Never true at the same time as
   *  `stale`: figures being rebuilt are not figures that are behind. */
  syncing: boolean;
  last_computed_at: string | null;
}

export interface PerformanceRow {
  sku: string;
  sku_normalized: string;
  /** The sheet's own "Total Count" column, beside Total Qty. */
  total_count: number;
  total_qty: number;
  total_orders: number;
  shopify_sales: number;
  /** A share of what the imported SKUs sold, so the column sums to 100%. Not
   *  the KPI card's percentage, which divides by the whole store. */
  shopify_sales_pct: number;
  total_complaints: number;
  status: SkuStatus;
  complaints: Record<string, number>;
}

export interface PerformancePage {
  rows: PerformanceRow[];
  complaint_columns: ComplaintColumn[];
  complaint_scope: ComplaintScope;
  total: number;
  limit: number;
  offset: number;
  days: number;
  sort: string;
  descending: boolean;
}
