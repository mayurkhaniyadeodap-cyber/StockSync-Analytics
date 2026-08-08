/**
 * The dashboard.
 *
 * Six cards and one table. Four of the cards and most of the table come from the
 * uploaded sheet; Shopify contributes units sold and that SKU's share of what
 * the sheet's SKUs sold, joined on the normalised SKU alone.
 *
 * The table is the shared `SkuTable` reading the same endpoint SKU Performance
 * reads, so the two pages cannot come to different conclusions about a SKU.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ComplaintScopeNote } from '../components/ComplaintScopeNote';
import { Icon } from '../components/Icon';
import { ShopifyWidget } from '../components/ShopifyWidget';
import { TrendScopeNote } from '../components/TrendScopeNote';
import { Skeleton } from '../components/Skeleton';
import { SyncStateNotice } from '../components/SyncStateNotice';
import { LineChart } from '../components/charts/LineChart';
import { RangePicker } from '../components/charts/RangePicker';
import type { Range } from '../components/charts/RangePicker';
import { ChartTooltipProvider } from '../contexts/ChartTooltipContext';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { useOnClickOutside } from '../hooks/useOnClickOutside';
import { useShopifyStatus } from '../hooks/useShopifyStatus';
import { useToast } from '../hooks/useToast';
import { StockSyncApiError, api } from '../lib/api';
import { freshness, n, pct } from '../lib/format';
import { SkuTable } from './analytics/SkuTable';
import { DEFAULT_DESCENDING, DEFAULT_SORT, TOP_SKUS } from './analytics/skuColumns';
import type {
  AnalyticsOverview,
  PerformancePage as SkuTablePage,
  Report,
  ReportFormat,
  Trend,
} from '../types/api';

/** The three the Export Centre already writes — same writers, same files. */
const EXPORT_FORMATS: { fmt: ReportFormat; label: string }[] = [
  { fmt: 'csv', label: 'CSV' },
  { fmt: 'xlsx', label: 'Excel' },
  { fmt: 'pdf', label: 'PDF' },
];

function labelsFor(trend: Trend): string[] {
  return trend.points.map((point) =>
    new Date(`${point.day}T00:00:00`).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
    }),
  );
}

function Card({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'warn' | 'bad';
}) {
  return (
    <div className={['kpi', 'static', tone ?? ''].filter(Boolean).join(' ')}>
      <span className="kpi-lbl">{label}</span>
      <span className="kpi-val">{value}</span>
      <span className="kpi-delta">{note}</span>
    </div>
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
        className="btn"
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
  // `changedAt` is the provider's signal that Shopify moved — a sync finishing,
  // a store connecting. The page reloads its figures from it, which covers the
  // sync that now runs after every import as well as one started from the
  // Shopify page. The page no longer starts syncs itself, so it does not need
  // the run.
  const { changedAt } = useShopifyStatus();

  const [range, setRange] = useState<Range>(30);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [table, setTable] = useState<SkuTablePage | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);

  const message = (caught: unknown, fallback: string) =>
    caught instanceof StockSyncApiError ? caught.message : fallback;

  const loadOverview = useCallback(async (days: number) => {
    setError(null);
    try {
      setOverview(await api.get<AnalyticsOverview>(`/analytics/overview?days=${String(days)}`));
    } catch (caught) {
      setError(message(caught, 'Could not load your analytics.'));
    }
  }, []);

  const loadTable = useCallback(async () => {
    setTableError(null);
    try {
      // The same endpoint SKU Performance reads, with the same sort. Two
      // sources for one table is how the two pages would start disagreeing
      // about a SKU; `/analytics/skus` has its own fixed ordering and none of
      // the per-category columns, so it cannot answer this table.
      const params = new URLSearchParams({
        days: String(range),
        limit: String(TOP_SKUS),
        sort: DEFAULT_SORT,
        descending: String(DEFAULT_DESCENDING),
      });
      if (query) params.set('search', query);
      setTable(await api.get<SkuTablePage>(`/analytics/performance?${params.toString()}`));
    } catch (caught) {
      setTableError(message(caught, 'Could not load the SKU table.'));
    }
  }, [query, range]);

  // `changedAt` covers connect, disconnect and verify as well as a finished
  // sync: all four change which Shopify sales these figures are drawn from, so
  // the cards and the table are re-read rather than left describing the
  // previous store.
  useEffect(() => {
    void loadOverview(range);
  }, [loadOverview, range, changedAt]);

  useEffect(() => {
    setTable(null);
    void loadTable();
  }, [loadTable, changedAt]);

  // Debounced so typing a SKU does not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const kpis = overview?.kpis;
  const loading = overview === null && error === null;

  const cards = useMemo(() => {
    if (!kpis) return [];
    return [
      {
        label: 'Total SKUs',
        value: n(kpis.total_skus),
        note: 'in the imported sheet',
      },
      {
        label: 'Total quantity',
        value: n(kpis.total_quantity),
        note: 'units on hand',
      },
      {
        label: 'Shopify sales',
        value: n(kpis.shopify_sales),
        note: `units matched by SKU · ${String(kpis.days)}d`,
      },
      {
        label: 'Shopify sales %',
        value: pct(kpis.shopify_sales_pct),
        // The card is a share, so the denominator is stated rather than implied.
        note: `of ${n(kpis.shopify_sales_all)} units sold`,
      },
      {
        label: 'Total orders',
        value: n(kpis.total_orders),
        note: 'from the sheet',
      },
      {
        label: 'Total complaints',
        value: n(kpis.total_complaints),
        note: 'across all categories',
        tone: kpis.total_complaints > 0 ? ('warn' as const) : undefined,
      },
    ];
  }, [kpis]);

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

        <div className="cardgrid">
          {loading
            ? Array.from({ length: 6 }, (_, i) => (
                <div className="kpi static" key={i} aria-busy="true">
                  <Skeleton height={11} width="60%" />
                  <Skeleton height={20} width="80%" style={{ marginTop: 10 }} />
                  <Skeleton height={9} width="50%" style={{ marginTop: 8 }} />
                </div>
              ))
            : cards.map((card) => <Card key={card.label} {...card} />)}
        </div>

        <ShopifyWidget />

        <div className="panel">
          <div className="p-hd">
            <h3>Shopify sales trend</h3>
            <div className="r">
              <RangePicker value={range} onChange={setRange} label="Sales trend" />
            </div>
          </div>
          <div className="chart-wrap" aria-busy={overview === null}>
            {overview ? (
              <LineChart
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
              Units sold
            </span>
            <span>
              <i style={{ background: 'var(--moss)' }} />
              Prior period
            </span>
          </div>
          <TrendScopeNote />
        </div>

        {/* Renders only when some complaints cannot answer the date range —
            an aggregated sheet carries no Complaint Date column to filter on,
            and a table headed "most complained" over a range must say so. */}
        <ComplaintScopeNote scope={table?.complaint_scope} />

        <div className="panel">
          <div className="p-hd">
            <h3>Top 50 Most Complained SKUs</h3>
            {/* Describes the rows below it, not the workspace: "1,641 rows"
                beside a table of 50 is a contradiction the reader has to
                resolve. The total is still stated, in the footer. */}
            {table ? (
              <span className="hint">
                {query ? 'Matching your search' : 'Ranked by total complaints'}
              </span>
            ) : null}
            <div className="r">
              <div className="search" style={{ maxWidth: 190 }}>
                <Icon name="search" size="s" />
                <input
                  className="inp"
                  aria-label="Filter by SKU"
                  placeholder="Filter by SKU"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  style={{
                    height: 30,
                    fontSize: 12.5,
                    paddingLeft: 32,
                    background: 'var(--paper)',
                  }}
                />
              </div>
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
              <p>No SKU in the sheet fits the current filter.</p>
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
                  ? query
                    ? `Showing ${n(table.rows.length)} of ${n(table.total)} matching SKUs`
                    : `Top ${n(table.rows.length)} most complained of ${n(table.total)} SKUs`
                  : `${n(table.total)} ${query ? 'matching ' : ''}SKU${table.total === 1 ? '' : 's'}`}
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
      </Page>
    </ChartTooltipProvider>
  );
}
