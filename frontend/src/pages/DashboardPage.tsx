/**
 * The dashboard.
 *
 * Six cards, two Shopify cards, three charts and two tables. Everything on it
 * comes from an endpoint that already existed: `/analytics/overview` for the
 * cards and the trend, `/analytics/insights` for the complaint mix, and
 * `/analytics/performance` — three times, with different filters — for the stock
 * split, the attention list and the table at the bottom.
 *
 * **One range governs the page.** It sits in the header rather than on the trend
 * chart, because it always drove the cards and the table as well as the chart,
 * and a control that changes six panels does not belong inside one of them.
 *
 * The table is the shared `SkuTable` reading the same endpoint SKU Performance
 * reads, with the same default sort, so the two pages cannot come to different
 * conclusions about a SKU.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ComplaintScopeNote } from '../components/ComplaintScopeNote';
import { Icon } from '../components/Icon';
import { KpiCard } from '../components/KpiCard';
import { ShopifyWidget } from '../components/ShopifyWidget';
import { Skeleton } from '../components/Skeleton';
import { SyncStateNotice } from '../components/SyncStateNotice';
import { LineChart } from '../components/charts/LineChart';
import { RangePicker } from '../components/charts/RangePicker';
import type { Range } from '../components/charts/RangePicker';
import { ChartTooltipProvider } from '../contexts/ChartTooltipContext';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { PanelHead } from '../components/shell/PanelHead';
import { useOnClickOutside } from '../hooks/useOnClickOutside';
import { useSharedRange } from '../hooks/useSharedRange';
import { useShopifyStatus } from '../hooks/useShopifyStatus';
import { useToast } from '../hooks/useToast';
import { API_BASE, StockSyncApiError, api, ensureSession } from '../lib/api';
import { freshness, n, pct, sharePct } from '../lib/format';
import { SkuTable } from './analytics/SkuTable';
import { STATUS_LABEL } from './analytics/status';
import { DEFAULT_DESCENDING, DEFAULT_SORT, TOP_SKUS } from './analytics/skuColumns';
import {
  AttentionPanel,
  BestSellersPanel,
  ComplaintBreakdownPanel,
  InventoryHealthPanel,
  RestockPanel,
} from './dashboard/panels';
import { complaintBreakdownNote, inventoryHealthNote } from './dashboard/notes';
import { useDashboardPanels } from './dashboard/useDashboardPanels';
import type { KpiCardProps, Trend as Trending } from '../components/KpiCard';
import type {
  AnalyticsOverview,
  ComplaintColumn,
  PerformancePage as SkuTablePage,
  Report,
  ReportFormat,
  SkuStatus,
  Trend,
} from '../types/api';

/** The three the Export Centre already writes — same writers, same files. */
const EXPORT_FORMATS: { fmt: ReportFormat; label: string }[] = [
  { fmt: 'csv', label: 'CSV' },
  { fmt: 'xlsx', label: 'Excel' },
  { fmt: 'pdf', label: 'PDF' },
];

const STATUSES: SkuStatus[] = ['excellent', 'good', 'attention', 'critical'];

/**
 * The Dashboard reads complaints as the sheet's whole record, not as a slice of
 * the date range.
 *
 * Everything else on this page except Shopify Sales is a snapshot of the newest
 * import — SKUs, quantity, orders, stock levels. A complaint total that moved
 * with the range while the order count beside it did not made Complaint Rate a
 * ratio of two different periods, and made "Total Complaints" mean something
 * different from the same words on the import screen.
 *
 * Complaint Analytics sends no basis and therefore keeps the windowed view,
 * where following the range is the page's entire purpose.
 */
const COMPLAINT_BASIS = 'total';

/**
 * The table's column filters, in the query shape the server already accepts.
 *
 * Deliberately the same five names SKU Performance sends. Filtering the
 * dashboard table and filtering the full table are the same operation on the
 * same endpoint, and a second spelling of `min_qty` is how they would drift.
 */
interface FilterState {
  search: string;
  status: string;
  category: string;
  minQty: string;
  minSalesPct: string;
}

const NO_FILTERS: FilterState = {
  search: '',
  status: '',
  category: '',
  minQty: '',
  minSalesPct: '',
};

/** Only the filters actually set become query parameters. */
function toQuery(filters: FilterState): URLSearchParams {
  const params = new URLSearchParams();
  const pairs: [string, string][] = [
    ['search', filters.search.trim()],
    ['status', filters.status],
    ['complaint_category', filters.category],
    ['min_qty', filters.minQty],
    ['min_sales_pct', filters.minSalesPct],
  ];
  for (const [key, value] of pairs) if (value !== '') params.set(key, value);
  return params;
}

function labelsFor(trend: Trend): string[] {
  return trend.points.map((point) =>
    new Date(`${point.day}T00:00:00`).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
    }),
  );
}

/**
 * Export the dashboard as it stands, in any of the three formats.
 *
 * The whole thing is a POST to `/reports` — the same endpoint, worker, builders
 * and writers the Export Centre uses, with `kind: 'dashboard'`. There is no
 * export code here and no second polling loop: the report lands in the Export
 * Centre, which already tracks preparing → ready and offers the download, so
 * this hands over to it rather than growing a copy of it.
 */
function ExportSnapshot({ days }: { days: Range }) {
  const navigate = useNavigate();
  const { toast } = useToast();

  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(false), []);
  useOnClickOutside(anchor, close, open);

  async function exportAs(fmt: ReportFormat, label: string) {
    setOpen(false);
    setBusy(true);
    try {
      await api.post<Report>('/reports', {
        kind: 'dashboard',
        fmt,
        // The range the cards are currently showing, so the file matches the
        // screen it was taken from rather than a default nobody chose.
        range_option: String(days),
      });
      toast(`Preparing your ${label} snapshot…`, 'slate');
      void navigate('/reports');
    } catch (caught) {
      toast(
        caught instanceof StockSyncApiError ? caught.message : 'Could not start that export.',
        'rust',
        true,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ position: 'relative' }} ref={anchor}>
      <button
        className="btn pri"
        onClick={() => setOpen((shown) => !shown)}
        disabled={busy}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Icon name="dl" size="s" /> {busy ? 'Exporting…' : 'Export snapshot'}
        <Icon name="down" size="s" style={{ opacity: 0.5 }} />
      </button>

      <div className={`pop${open ? ' on' : ''}`} role="menu">
        <div className="pop-hd">
          <div style={{ fontWeight: 600, fontSize: 13 }}>Export snapshot</div>
          <div style={{ fontSize: 11.5, color: 'var(--ink-45)' }}>
            The figures above, last {days} days
          </div>
        </div>
        {EXPORT_FORMATS.map((option) => (
          <button
            key={option.fmt}
            className="pop-item"
            role="menuitem"
            onClick={() => void exportAs(option.fmt, option.label)}
          >
            <Icon name="file" size="s" /> {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  // `changedAt` is the provider's signal that Shopify moved — a sync finishing,
  // a store connecting. The page reloads its figures from it, which covers the
  // sync that now runs after every import as well as one started from the
  // Shopify page. The page no longer starts syncs itself, so it does not need
  // the run.
  const { changedAt } = useShopifyStatus();

  // The window the header's date control sets. Shared rather than local so the
  // two controls that show it — the header and the segmented one on the trend
  // card — are one setting instead of two that can disagree on screen.
  const { days: range, setDays: setRange } = useSharedRange();
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [filters, setFilters] = useState<FilterState>(NO_FILTERS);
  const [applied, setApplied] = useState<FilterState>(NO_FILTERS);
  const [table, setTable] = useState<SkuTablePage | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);
  // Held separately so the category list survives a filter that returns no rows.
  const [columns, setColumns] = useState<ComplaintColumn[]>([]);

  const panels = useDashboardPanels(range, changedAt, overview?.kpis.low_stock_threshold);

  const message = (caught: unknown, fallback: string) =>
    caught instanceof StockSyncApiError ? caught.message : fallback;

  const loadOverview = useCallback(async (days: number) => {
    setError(null);
    try {
      setOverview(
        await api.get<AnalyticsOverview>(
          `/analytics/overview?days=${String(days)}&complaints=${COMPLAINT_BASIS}`,
        ),
      );
    } catch (caught) {
      setError(message(caught, 'Could not load your analytics.'));
    }
  }, []);

  /** The query the table and its export share, minus paging. */
  const query = useMemo(() => {
    const params = toQuery(applied);
    params.set('days', String(range));
    params.set('complaints', COMPLAINT_BASIS);
    // The same endpoint SKU Performance reads, with the same sort. Two sources
    // for one table is how the two pages would start disagreeing about a SKU;
    // `/analytics/skus` has its own fixed ordering and none of the per-category
    // columns, so it cannot answer this table.
    params.set('sort', DEFAULT_SORT);
    params.set('descending', String(DEFAULT_DESCENDING));
    return params;
  }, [applied, range]);

  const loadTable = useCallback(async () => {
    setTableError(null);
    try {
      const params = new URLSearchParams(query);
      params.set('limit', String(TOP_SKUS));
      const page = await api.get<SkuTablePage>(`/analytics/performance?${params.toString()}`);
      setTable(page);
      if (page.complaint_columns.length > 0) setColumns(page.complaint_columns);
    } catch (caught) {
      setTableError(message(caught, 'Could not load the SKU table.'));
    }
  }, [query]);

  // `changedAt` covers connect, disconnect and verify as well as a finished
  // sync: all four change which Shopify sales these figures are drawn from, so
  // the cards and the table are re-read rather than left describing the
  // previous store.
  useEffect(() => {
    void loadOverview(range);
  }, [loadOverview, range, changedAt]);

  // Same rule as the panels: the rows on screen stay until new ones arrive.
  // Blanking the table on every filter keystroke and every finished sync made a
  // correct table flicker through a skeleton for no reason.
  useEffect(() => {
    void loadTable();
  }, [loadTable, changedAt]);

  // Debounced so typing a SKU does not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setApplied(filters), 300);
    return () => clearTimeout(timer);
  }, [filters]);

  const set = (key: keyof FilterState) => (value: string) =>
    setFilters((current) => ({ ...current, [key]: value }));

  const activeFilters = useMemo(
    () => Object.values(filters).filter((value) => value !== '').length,
    [filters],
  );

  /**
   * A plain navigation rather than a fetch: the browser handles the file, the
   * filename comes from Content-Disposition, and the session cookie rides along
   * because it is same-origin. The export carries the *whole* filtered set, not
   * the fifty rows on screen.
   */
  const download = async (fmt: 'csv' | 'xlsx') => {
    // A navigation gets no second chance at a 401 the way a fetch does, so the
    // session is renewed first if it is close to expiring.
    if (!(await ensureSession())) return;
    const params = new URLSearchParams(query);
    params.set('format', fmt);
    window.location.assign(`${API_BASE}/analytics/performance/export?${params.toString()}`);
    toast(`Preparing ${fmt.toUpperCase()} of ${n(table?.total ?? 0)} SKUs…`, 'slate');
  };

  const kpis = overview?.kpis;
  const loading = overview === null && error === null;

  /**
   * The one card that can carry a real trend.
   *
   * `trend.previous` is the only prior period the API reports — the same window
   * shifted back, computed server-side. The other five cards are sheet totals
   * from the newest import, and an import replaces the whole dataset, so there
   * is no previous value to compare them against. Rather than invent one, those
   * cards carry a note saying what they count.
   */
  const salesTrend = useMemo((): Trending | undefined => {
    if (!overview) return undefined;
    const sum = (points: { units: number }[]) =>
      points.reduce((total, p) => total + p.units, 0);
    const now = sum(overview.trend.points);
    const before = sum(overview.trend.previous);
    // No prior sales is not a 100% rise — there is nothing to divide by.
    if (before === 0) return undefined;
    const change = ((now - before) / before) * 100;
    return {
      value: pct(Math.abs(change)),
      up: change >= 0,
      good: change >= 0,
      against: `vs. previous ${String(range)} days`,
    };
  }, [overview, range]);

  const cards = useMemo((): KpiCardProps[] => {
    if (!kpis) return [];
    const rate =
      kpis.total_orders > 0 ? sharePct(kpis.total_complaints, kpis.total_orders) : null;

    return [
      {
        label: 'Total SKUs',
        value: n(kpis.total_skus),
        icon: 'layers',
        note: 'in the imported sheet',
      },
      {
        label: 'Total Quantity',
        value: n(kpis.total_quantity),
        icon: 'box',
        tone: 'clay',
        note: 'units on hand',
      },
      {
        label: 'Shopify Sales',
        value: n(kpis.shopify_sales),
        icon: 'bag',
        tone: 'moss',
        trend: salesTrend,
        // The share and its denominator, which used to be a card of their own.
        // Folded in here because the figure only means anything beside the
        // count it is a share of, and a sixth card was needed for the rate.
        // Kept alongside the trend rather than replaced by it: they answer
        // different questions — how much it moved, and how much of the store
        // this is.
        // The window is named on the card, because this is the only figure in
        // the row the date range moves. Every other card is a snapshot of the
        // newest import.
        note: `${pct(kpis.shopify_sales_pct)} of ${n(kpis.shopify_sales_all)} units sold · last ${String(range)} days`,
      },
      {
        label: 'Total Orders',
        value: n(kpis.total_orders),
        icon: 'file',
        note: 'from the sheet',
      },
      {
        label: 'Total Complaints',
        value: n(kpis.total_complaints),
        icon: 'warn',
        tone: 'rust',
        note: 'across all categories',
        alert: kpis.total_complaints > 0 ? ('warn' as const) : undefined,
      },
      {
        label: 'Complaint Rate',
        // Both figures come from the same sheet, so they are commensurable —
        // and with no orders there is no rate, which is not the same as a rate
        // of zero. The denominator is named on the card rather than left to be
        // guessed, because "3.2%" of an unstated whole is not a figure.
        value: rate === null ? '—' : pct(rate),
        icon: 'pct',
        tone: 'amber',
        // Both bases named, because they are not the same base. Orders are a
        // snapshot of the newest import; complaints follow the date range for
        // the SKUs that were imported with dates on them, so the range can
        // move the numerator without touching the denominator.
        note:
          rate === null
            ? 'no orders in the sheet to measure against'
            : `of ${n(kpis.total_orders)} orders in the sheet`,
        alert: rate !== null && rate >= 5 ? ('bad' as const) : undefined,
      },
    ];
  }, [kpis, range, salesTrend]);

  if (overview && !overview.has_data) {
    return (
      <Page>
        <PageHeader
          title="Dashboard"
          subtitle="Your inventory sheet, enriched with Shopify sales"
        />
        <div className="panel">
          <div className="empty">
            <div className="ei">
              <Icon name="layers" size="l" />
            </div>
            <h3>No data yet</h3>
            <p>
              Import an inventory sheet to begin. Once a store is connected, Shopify sales are
              matched onto your SKUs automatically.
            </p>
            <div className="acts">
              <button className="btn pri" onClick={() => void navigate('/import')}>
                Import inventory
              </button>
              <button className="btn sec" onClick={() => void navigate('/shopify')}>
                Connect Shopify
              </button>
            </div>
          </div>
        </div>

        {/* Shown here too: with no sheet imported the store may still be
            connected and syncing, and that is worth seeing. */}
        <ShopifyWidget />
      </Page>
    );
  }

  const subtitle = kpis?.last_computed_at
    ? `Sales figures computed ${freshness(new Date(kpis.last_computed_at))}`
    : 'Your inventory sheet, enriched with Shopify sales';

  return (
    <ChartTooltipProvider>
      <Page>
        <PageHeader
          title="Dashboard"
          subtitle={subtitle}
          actions={<ExportSnapshot days={range} />}
        />

        {error ? (
          <div style={{ marginBottom: 18 }}>
            <div className="inline-err">
              <Icon name="warn" />
              <div>
                <b>Couldn&rsquo;t load analytics.</b> {error}
              </div>
              <button className="btn sm" onClick={() => void loadOverview(range)}>
                <Icon name="refresh" size="s" /> Retry
              </button>
            </div>
          </div>
        ) : null}

        <SyncStateNotice
          syncing={kpis?.syncing}
          stale={kpis?.stale}
          onRetryStarted={() => void Promise.all([loadOverview(range), loadTable()])}
        />

        {/* Stated once, where the figures start. The range control sits on the
            trend panel below, and without this the six cards look like six
            figures for the chosen period when only one of them is. */}
        <div className="range-scope">
          <Icon name="clock" size="s" />
          <span>
            The date range applies to <b>Shopify Sales</b> and <b>Shopify Sales %</b> only.
            Every other figure on this page — SKUs, quantity, orders, complaints and stock
            levels — comes from your most recent import and does not move with it.
          </span>
        </div>

        <div className="cardgrid six">
          {loading
            ? Array.from({ length: 6 }, (_, i) => (
                <div className="kpi static" key={i} aria-busy="true">
                  <Skeleton height={34} width={34} radius={8} />
                  <Skeleton height={20} width="80%" style={{ marginTop: 14 }} />
                  <Skeleton height={11} width="55%" style={{ marginTop: 10 }} />
                </div>
              ))
            : cards.map((card) => <KpiCard key={card.label} {...card} />)}
        </div>

        {/* The store's own state, on its own row: whether the figures above are
            current is part of reading them, and squeezed into a 296px rail the
            two cards had no room for the labels that say so. */}
        <ShopifyWidget />

        <div className="grid3">
          <div className="panel">
            <PanelHead
              title="Shopify Sales Trend"
              icon="chart"
              hint="Units sold through Shopify"
              info={
                <>
                  This trend shows <b>all Shopify sales</b> from your store. The Shopify Sales
                  figure above counts only SKUs imported into StockSync Analytics.
                </>
              }
              actions={<RangePicker value={range} onChange={setRange} label="Sales trend" />}
            />
            <div className="chart-wrap" aria-busy={overview === null}>
              {overview ? (
                <LineChart
                  compact
                  caption={`Units sold per day over the last ${String(range)} days`}
                  labels={labelsFor(overview.trend)}
                  series={[
                    {
                      name: 'Units sold',
                      color: 'var(--slate)',
                      values: overview.trend.points.map((p) => p.units),
                      fill: true,
                    },
                    {
                      name: 'Prior period',
                      color: 'var(--moss)',
                      values: overview.trend.previous.map((p) => p.units),
                      dashed: true,
                    },
                  ]}
                />
              ) : (
                <Skeleton height={200} />
              )}
            </div>
            <div className="legend">
              <span>
                <i style={{ background: 'var(--slate)' }} />
                This period
              </span>
              <span>
                <i style={{ background: 'var(--moss)' }} />
                Previous period
              </span>
            </div>
          </div>

          <div className="panel">
            <PanelHead
              title="Inventory Health"
              icon="box"
              tone="moss"
              hint="Stock and sales distribution"
              info={inventoryHealthNote(kpis, panels.stock)}
              actions={
                <button
                  className="btn sm"
                  onClick={() => void navigate('/analytics/inventory')}
                >
                  Details <Icon name="right" size="s" />
                </button>
              }
            />
            <InventoryHealthPanel
              kpis={kpis}
              stock={panels.stock}
              error={error}
              onRetry={() => void loadOverview(range)}
            />
          </div>

          <div className="panel">
            <PanelHead
              title="Complaint Breakdown"
              icon="warn"
              tone="rust"
              hint="By category"
              info={complaintBreakdownNote(panels.insights)}
              actions={
                <button
                  className="btn sm"
                  onClick={() => void navigate('/analytics/complaints')}
                >
                  Details <Icon name="right" size="s" />
                </button>
              }
            />
            <ComplaintBreakdownPanel
              insights={panels.insights}
              error={panels.insightsError}
              onRetry={panels.reload}
            />
          </div>
        </div>

        {/* Renders only when some complaints cannot answer the date range —
            an aggregated sheet carries no Complaint Date column to filter on,
            and a table headed "most complained" over a range must say so. */}
        <ComplaintScopeNote scope={table?.complaint_scope} />

        {/* What the imported sheet sold, and what it is about to run out of.
            Both read off the insights payload the page already has, so neither
            panel costs a request of its own. */}
        <div className="grid-tables">
          <div className="panel">
            <PanelHead
              title="Best-Selling Products"
              icon="up"
              tone="moss"
              hint="Top imported SKUs by units sold through Shopify"
              actions={
                <button className="btn sm" onClick={() => void navigate('/analytics/sales')}>
                  Details <Icon name="right" size="s" />
                </button>
              }
            />
            <BestSellersPanel
              insights={panels.insights}
              error={panels.insightsError}
              onRetry={panels.reload}
            />
          </div>

          <div className="panel">
            <PanelHead
              title="Needs Restocking"
              icon="box"
              tone="amber"
              hint="Selling above the median on below-median stock"
              info="Both cuts are this workspace's own medians, not fixed thresholds — a constant that suits one store suits no other. Quantities come from your most recent import; units sold follow the selected range."
              actions={
                <button
                  className="btn sm"
                  onClick={() => void navigate('/analytics/inventory')}
                >
                  Details <Icon name="right" size="s" />
                </button>
              }
            />
            <RestockPanel
              insights={panels.insights}
              error={panels.insightsError}
              onRetry={panels.reload}
            />
          </div>
        </div>

        <div className="grid-tables">
          <div className="panel">
            <PanelHead
              title="Products Requiring Attention"
              icon="warn"
              tone="rust"
              hint="Top SKUs by issues"
              actions={
                <button
                  className="btn sm"
                  onClick={() => void navigate('/analytics/inventory')}
                >
                  View all <Icon name="right" size="s" />
                </button>
              }
            />
            <AttentionPanel
              rows={panels.attention}
              error={panels.attentionError}
              onRetry={panels.reload}
            />
          </div>

          <div className="panel">
            <div className="p-hd">
              <span className="p-chip" aria-hidden="true">
                <Icon name="layers" size="s" />
              </span>
              <h3>Product Performance</h3>
              {/* Describes the rows below it, not the workspace: "1,641 rows"
                beside a table of 50 is a contradiction the reader has to
                resolve. The total is still stated, in the footer. */}
              {table ? (
                <span className="hint">
                  {activeFilters > 0 ? 'Matching your filters' : 'Ranked by total complaints'}
                </span>
              ) : null}
              <div className="r">
                {activeFilters > 0 ? (
                  <button className="btn sm" onClick={() => setFilters(NO_FILTERS)}>
                    <Icon name="x" size="s" /> Clear {activeFilters} filter
                    {activeFilters === 1 ? '' : 's'}
                  </button>
                ) : null}
                <button
                  className="btn sm"
                  onClick={() => void download('csv')}
                  disabled={!table || table.total === 0}
                >
                  <Icon name="dl" size="s" /> CSV
                </button>
                <button
                  className="btn sm"
                  onClick={() => void download('xlsx')}
                  disabled={!table || table.total === 0}
                >
                  <Icon name="dl" size="s" /> Excel
                </button>
              </div>
              <span className="hint">All SKUs with sales, stock and complaint metrics</span>
            </div>

            <div className="p-bd">
              <div className="filters">
                <div className="search">
                  <Icon name="search" size="s" />
                  <input
                    className="inp"
                    aria-label="Filter by SKU"
                    placeholder="Filter by SKU"
                    value={filters.search}
                    onChange={(event) => set('search')(event.target.value)}
                  />
                </div>

                <select
                  className="inp"
                  aria-label="Status"
                  value={filters.status}
                  onChange={(event) => set('status')(event.target.value)}
                >
                  <option value="">Any status</option>
                  {STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {STATUS_LABEL[status]}
                    </option>
                  ))}
                </select>

                <select
                  className="inp"
                  aria-label="Complaint category"
                  value={filters.category}
                  onChange={(event) => set('category')(event.target.value)}
                >
                  <option value="">Any complaint</option>
                  {columns.map((column) => (
                    <option key={column.field} value={column.field}>
                      {column.header}
                    </option>
                  ))}
                </select>

                <input
                  className="inp num"
                  type="number"
                  min={0}
                  aria-label="Minimum quantity"
                  placeholder="Qty ≥"
                  value={filters.minQty}
                  onChange={(event) => set('minQty')(event.target.value)}
                />

                <input
                  className="inp num"
                  type="number"
                  min={0}
                  max={100}
                  step="0.01"
                  aria-label="Minimum Shopify sales percent"
                  placeholder="Sales % ≥"
                  value={filters.minSalesPct}
                  onChange={(event) => set('minSalesPct')(event.target.value)}
                />
              </div>
            </div>

            {tableError ? (
              <div className="p-bd">
                <div className="inline-err">
                  <Icon name="warn" />
                  <div>{tableError}</div>
                  <button className="btn sm" onClick={() => void loadTable()}>
                    Retry
                  </button>
                </div>
              </div>
            ) : table === null ? (
              <div className="p-bd" aria-busy="true">
                {[0, 1, 2, 3, 4].map((row) => (
                  <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
                ))}
              </div>
            ) : table.rows.length === 0 ? (
              <div className="empty">
                <div className="ei">
                  <Icon name="search" size="l" />
                </div>
                <h3>Nothing matches</h3>
                <p>No SKU in the sheet fits the current filters.</p>
              </div>
            ) : (
              <SkuTable rows={table.rows} maxHeight={560} />
            )}

            {/* No pager: the table is the top 50 and stops there. The count still
              says what was left out, because a list that quietly ends at 50
              reads as "these are all your SKUs" — which is the one thing it is
              not. The full set is on SKU performance and in every export. */}
            {table && table.rows.length > 0 ? (
              <div className="tbl-ft">
                <span>
                  {table.total > table.rows.length
                    ? activeFilters > 0
                      ? `Showing ${n(table.rows.length)} of ${n(table.total)} matching SKUs`
                      : `Top ${n(table.rows.length)} most complained of ${n(table.total)} SKUs`
                    : `${n(table.total)} ${activeFilters > 0 ? 'matching ' : ''}SKU${table.total === 1 ? '' : 's'}`}
                </span>
                <span className="spacer" />
                <button
                  className="btn sm"
                  onClick={() => void navigate('/analytics/performance')}
                >
                  See all SKUs <Icon name="right" size="s" />
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </Page>
    </ChartTooltipProvider>
  );
}
