/**
 * The dashboard's panels.
 *
 * Kept out of `DashboardPage` so the page reads as a layout rather than as a
 * thousand lines of chart configuration, and kept together because they share
 * one rule: **a panel with nothing to say says so.** None of them renders a
 * zeroed chart or a dashed row — an empty donut and a donut of zeros look
 * identical, and only one of them is honest.
 */

import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { BarChart } from '../../components/charts/BarChart';
import { DonutChart } from '../../components/charts/DonutChart';
import { complaintRate, n, pct, sharePct } from '../../lib/format';
import type { AnalyticsInsights, Kpis, PerformanceRow } from '../../types/api';
import { RankingTable, StatusBadge } from '../analytics/parts';
import { ATTENTION_ROWS } from './useDashboardPanels';
import type { StockSplit } from './useDashboardPanels';

function Loading({ height = 150 }: { height?: number }) {
  return (
    <div className="p-bd" aria-busy="true">
      <Skeleton height={height} />
    </div>
  );
}

/** A complaint rate for a table cell: capped at 100%, an em dash when there is none. */
function rateCell(complaints: number, orders: number): string {
  const rate = complaintRate(complaints, orders);
  return rate === null ? '—' : pct(rate);
}

function Failed({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="p-bd">
      <div className="inline-err">
        <Icon name="warn" />
        <div>{error}</div>
        <button className="btn sm" onClick={onRetry}>
          <Icon name="refresh" size="s" /> Retry
        </button>
      </div>
    </div>
  );
}

/**
 * Inventory health — how the sheet's own quantity column is distributed.
 *
 * Three buckets, built from the KPI payload the page already has plus one count,
 * using the *same* threshold that payload reports. This panel and the low-stock
 * figure elsewhere therefore cannot disagree about what "low" means.
 *
 * Out of stock is the only bucket needing its own request. When it fails the
 * panel shows the two buckets it can still stand behind and says so, rather than
 * folding an unknown count into "in stock" — which would report every empty SKU
 * as healthy.
 */
export function InventoryHealthPanel({
  kpis,
  stock,
  error,
  onRetry,
}: {
  kpis: Kpis | undefined;
  stock: StockSplit;
  /** Why the KPI payload this panel reads its totals from could not be had. */
  error?: string | null;
  onRetry?: () => void;
}) {
  /*
   * This panel's totals ride on the KPI payload, so when that read fails there
   * is nothing here to draw. It used to render a skeleton in that case and keep
   * it up for ever — a failed request looked identical to a slow one, which is
   * the worst of both.
   */
  if (error && onRetry) return <Failed error={error} onRetry={onRetry} />;
  if (!kpis) return <Loading />;

  const { outOfStock, stockedNoSales } = stock;
  const out = outOfStock ?? 0;
  const idle = stockedNoSales ?? 0;
  const low = kpis.low_stock;
  // Clamped: the buckets are counted by separate queries over the same column,
  // and a negative remainder would be a bug rendered as a slice.
  const selling = Math.max(kpis.total_skus - low - out - idle, 0);

  // Only the buckets that were actually read. A count that failed is left out
  // rather than folded into its neighbour, which would report unknown SKUs as
  // healthy ones.
  const slices = [
    { label: 'With stock & sales', value: selling, color: 'var(--moss)' },
    ...(stockedNoSales === null
      ? []
      : [{ label: 'With stock, no sales', value: idle, color: 'var(--slate)' }]),
    {
      label: `Low stock (< ${n(kpis.low_stock_threshold)})`,
      value: low,
      color: 'var(--amber)',
    },
    ...(outOfStock === null ? [] : [{ label: 'No stock', value: out, color: 'var(--rust)' }]),
  ].filter((slice) => slice.value > 0);

  if (kpis.total_skus === 0 || slices.length === 0) {
    return (
      <div className="empty">
        <div className="ei">
          <Icon name="box" size="l" />
        </div>
        <h3>No stock to report</h3>
        <p>Import an inventory sheet and its quantities appear here.</p>
      </div>
    );
  }

  return (
    <>
      <div className="p-bd donut-row">
        <div className="chart-wrap">
          <DonutChart
            compact
            caption="SKUs by stock level, from the imported sheet"
            centerValue={n(kpis.total_skus)}
            centerLabel="SKUs"
            slices={slices}
          />
        </div>
        {/* A keyed legend rather than hover-only labels: a share you have to
            find with a pointer is a share most readers never see. */}
        <ul className="donut-key">
          {slices.map((slice) => (
            <li key={slice.label}>
              <i style={{ background: slice.color }} />
              <span className="k-lbl">{slice.label}</span>
              <span className="k-pct num">{pct(sharePct(slice.value, kpis.total_skus))}</span>
              <span className="k-val num">{n(slice.value)}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

/**
 * Complaint breakdown — the sheet's complaint columns as a mix.
 *
 * A mix and not a trend: an aggregated sheet carries complaint totals per SKU
 * with no dates on them, so there is nothing to plot against time. Saying that
 * beats an empty axis.
 */
export function ComplaintBreakdownPanel({
  insights,
  error,
  onRetry,
}: {
  insights: AnalyticsInsights | null;
  error: string | null;
  onRetry: () => void;
}) {
  if (error) return <Failed error={error} onRetry={onRetry} />;
  if (!insights) return <Loading />;

  const categories = insights.complaints.categories.filter((category) => category.count > 0);

  if (categories.length === 0) {
    return (
      <div className="empty">
        <div className="ei">
          <Icon name="check" size="l" />
        </div>
        <h3>No complaints recorded</h3>
        <p>Nothing in the imported sheet carries a complaint against it.</p>
      </div>
    );
  }

  /*
   * The top five, and the rest as one row.
   *
   * Ten categories is a chart the eye has to read rather than see, and the tail
   * is usually single digits. "Other" is a real sum of the real remainder, and
   * it names how many categories it stands for so nothing looks hidden.
   */
  const TOP = 5;
  const head = categories.slice(0, TOP);
  const tail = categories.slice(TOP);
  const tailTotal = tail.reduce((sum, category) => sum + category.count, 0);

  const rows = [
    ...head.map((category) => ({
      label: category.label,
      value: category.count,
      meta: pct(category.share_pct),
      note: 'Complaints',
    })),
    ...(tailTotal > 0
      ? [
          {
            label: `Other (${String(tail.length)})`,
            value: tailTotal,
            meta: pct(sharePct(tailTotal, insights.complaints.total_complaints)),
            note: 'Complaints',
            detail: tail.map((category) => category.label).join(', '),
          },
        ]
      : []),
  ];

  return (
    <>
      <div className="chart-wrap">
        <BarChart compact caption="Complaints by category" rows={rows} color="var(--rust)" />
      </div>
    </>
  );
}

/**
 * Products Requiring Attention — the SKUs the server marked Critical or Needs
 * attention, worst first.
 *
 * The verdict is read off the row, not recomputed. Deriving "what counts as
 * critical" here would give the dashboard a second opinion, and the server's is
 * the one every other page and every export already uses.
 */
export function AttentionPanel({
  rows,
  error,
  onRetry,
}: {
  rows: PerformanceRow[] | null;
  error: string | null;
  onRetry: () => void;
}) {
  const navigate = useNavigate();

  if (error) return <Failed error={error} onRetry={onRetry} />;
  if (!rows) {
    return (
      <div className="p-bd" aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="empty">
        <div className="ei">
          <Icon name="check" size="l" />
        </div>
        <h3>Nothing needs attention</h3>
        <p>No SKU is marked Critical or Needs attention in this window.</p>
      </div>
    );
  }

  return (
    <>
      <div className="tbl-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th className="no-sort">SKU</th>
              <th className="n no-sort">Sales</th>
              <th className="n no-sort">Complaints</th>
              <th className="n no-sort">Rate</th>
              <th className="no-sort">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.sku_normalized}>
                <td>
                  <span className="num">{row.sku}</span>
                </td>
                <td className="n">{n(row.shopify_sales)}</td>
                <td className="n">{n(row.total_complaints)}</td>
                {/* Complaints against the sheet's own order count. Both figures
                    come from the same import, so the ratio is comparable; with
                    no orders there is no rate, which is not a rate of zero.
                    Capped at 100% by `complaintRate` — a SKU with several
                    complaints per order really does exceed it. */}
                <td className="n">{rateCell(row.total_complaints, row.total_orders)}</td>
                <td>
                  <StatusBadge status={row.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tbl-ft">
        <span>
          {rows.length === ATTENTION_ROWS
            ? `The ${String(ATTENTION_ROWS)} worst-ranked SKUs in this window`
            : `${n(rows.length)} SKU${rows.length === 1 ? '' : 's'} needing attention`}
        </span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => void navigate('/analytics/inventory')}>
          Inventory insights <Icon name="right" size="s" />
        </button>
      </div>
    </>
  );
}

/**
 * Best-Selling Products — the imported SKUs that sold the most in this window.
 *
 * Read straight off `rankings.top_selling`, which the page already has: the
 * server ranks by units sold, descending, and drops anything that sold nothing,
 * so a store with no sales gets an empty list rather than ten rows of zeroes.
 * Ranking here instead would be a second opinion about "best selling", and the
 * first one is what Sales Analytics and every export already use.
 *
 * Available stock sits beside the sales figure because they are one decision:
 * a SKU selling hard on three units left is the most urgent row on the page.
 */
export function BestSellersPanel({
  insights,
  error,
  onRetry,
}: {
  insights: AnalyticsInsights | null;
  error: string | null;
  onRetry: () => void;
}) {
  const navigate = useNavigate();

  if (error) return <Failed error={error} onRetry={onRetry} />;
  if (!insights) {
    return (
      <div className="p-bd" aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
        ))}
      </div>
    );
  }

  const rows = insights.rankings.top_selling;

  return (
    <>
      <RankingTable
        rows={rows}
        measure="sales"
        emptyNote="No imported SKU has sold a unit in this window. Once a sync pulls orders that match your sheet, the best sellers appear here."
      />
      {rows.length > 0 ? (
        <div className="tbl-ft">
          <span>
            Ranked by units sold · {n(insights.sales.shopify_sales)} units across every imported
            SKU
          </span>
          <span className="spacer" />
          <button className="btn sm" onClick={() => void navigate('/analytics/sales')}>
            Sales analytics <Icon name="right" size="s" />
          </button>
        </div>
      ) : null}
    </>
  );
}

/**
 * Needs Restocking — selling above the median on below-median stock.
 *
 * The cut is the server's, and it is *relative*: "low stock" measured against a
 * constant is wrong for every store but the one the constant was written for,
 * so the comparison is with this workspace's own median. The footer states both
 * medians, because a list headed "needs restocking" means nothing until you
 * know what it was measured against.
 *
 * The zero-sales count rides along rather than getting a panel of its own: it
 * is the same question from the other end — stock that is not moving — and one
 * figure with a way through to the full list answers it here.
 */
export function RestockPanel({
  insights,
  error,
  onRetry,
}: {
  insights: AnalyticsInsights | null;
  error: string | null;
  onRetry: () => void;
}) {
  const navigate = useNavigate();

  if (error) return <Failed error={error} onRetry={onRetry} />;
  if (!insights) {
    return (
      <div className="p-bd" aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
        ))}
      </div>
    );
  }

  const {
    low_stock_high_sales: rows,
    median_qty,
    median_sales,
    zero_sales_total,
  } = insights.inventory;

  return (
    <>
      <RankingTable
        rows={rows}
        measure="stock"
        emptyNote="No SKU is selling above the median on below-median stock. Nothing here needs reordering on these figures."
      />
      <div className="tbl-ft">
        <span>
          {rows.length > 0
            ? `Under ${n(Math.round(median_qty))} units held, over ${n(Math.round(median_sales))} sold`
            : 'Measured against this workspace’s own medians'}
          {zero_sales_total > 0
            ? ` · ${n(zero_sales_total)} SKU${zero_sales_total === 1 ? '' : 's'} hold stock and sold nothing`
            : ''}
        </span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => void navigate('/analytics/inventory')}>
          Inventory analytics <Icon name="right" size="s" />
        </button>
      </div>
    </>
  );
}
