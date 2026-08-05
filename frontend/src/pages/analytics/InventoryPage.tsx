/**
 * Inventory Insights — stock read against demand, with what to do about it.
 *
 * Six findings, each a list of SKUs and a recommendation. The cuts are made at the
 * *median* of this workspace's own SKUs rather than at a constant, and each panel
 * states the cut it used — "high stock" means nothing until you know what it was
 * measured against.
 *
 * Restock Needed and Overstocked Items come from the generated insight cards,
 * which name a single SKU each; the four list panels come from the ranked
 * findings. Both are derived server-side from one read, so nothing here can
 * disagree with the Sales or Complaint pages.
 */

import { Skeleton } from '../../components/Skeleton';
import { n } from '../../lib/format';
import { AnalyticsFrame } from './AnalyticsFrame';
import { Empty, InsightCard, RankingTable } from './parts';
import { useInsights } from './useInsights';

/** What to do about each finding, stated once beside the list it applies to. */
const RECOMMENDATION: Record<string, { badge: string; tone: string; note: string }> = {
  overstocked: {
    badge: 'Slow down buying',
    tone: 'amber',
    note: 'Above-median stock on below-median sales. Hold off reordering until it moves.',
  },
  understocked: {
    badge: 'Reorder',
    tone: 'rust',
    note: 'Selling above the median on below-median stock. These run out first.',
  },
  dead: {
    badge: 'Review or discount',
    tone: 'clay',
    note: 'Holding stock that has not sold a single unit in this window.',
  },
  complaints: {
    badge: 'Investigate quality',
    tone: 'rust',
    note: 'Highest defect rates against the sheet’s own order counts.',
  },
};

function Recommendation({ kind }: { kind: keyof typeof RECOMMENDATION }) {
  const entry = RECOMMENDATION[kind];
  if (!entry) return null;
  return (
    <div className="p-ft">
      <span className={`badge ${entry.tone}`}>{entry.badge}</span>
      <span className="hint" style={{ marginLeft: 8 }}>
        {entry.note}
      </span>
    </div>
  );
}

export function InventoryPage() {
  const state = useInsights();
  const { insights } = state;
  const inventory = insights?.inventory;

  const cut = (over: boolean) =>
    inventory
      ? `${over ? 'Over' : 'Under'} ${n(Math.round(inventory.median_qty))} units, ${
          over ? 'under' : 'over'
        } ${n(Math.round(inventory.median_sales))} sold`
      : '';

  const loadingPanel = (
    <div className="p-bd" aria-busy="true">
      <Skeleton height={130} />
    </div>
  );

  // The two single-SKU recommendations. Absent when the data does not support
  // them, rather than shown with a dash.
  const restock = insights?.quick.find((card) => card.key === 'restock');
  const overstocked = insights?.quick.find((card) => card.key === 'overstocked');

  return (
    <AnalyticsFrame
      title="Inventory Insights"
      subtitle="Stock health and what to do next"
      state={state}
    >
      {insights && (restock || overstocked) ? (
        <div className="panel">
          <div className="p-hd">
            <h3>Recommendations</h3>
            <span className="hint">The single most urgent SKU in each direction</span>
          </div>
          <div className="p-bd">
            <div className="grid2" style={{ marginBottom: 0 }}>
              {restock ? (
                <InsightCard insight={{ ...restock, title: 'Restock needed' }} />
              ) : null}
              {overstocked ? (
                <InsightCard insight={{ ...overstocked, title: 'Overstocked item' }} />
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid2">
        <div className="panel">
          <div className="p-hd">
            <h3>High stock, low sales</h3>
            {inventory ? <span className="hint">{cut(true)}</span> : null}
          </div>
          {inventory ? (
            <>
              <RankingTable
                rows={inventory.high_stock_low_sales}
                measure="stock"
                emptyNote="No SKU is holding above-median stock on below-median sales."
              />
              {inventory.high_stock_low_sales.length > 0 ? (
                <Recommendation kind="overstocked" />
              ) : null}
            </>
          ) : (
            loadingPanel
          )}
        </div>

        <div className="panel">
          <div className="p-hd">
            <h3>Low stock, high sales</h3>
            {inventory ? <span className="hint">{cut(false)}</span> : null}
          </div>
          {inventory ? (
            <>
              <RankingTable
                rows={inventory.low_stock_high_sales}
                measure="stock"
                emptyNote="No SKU is selling above the median on below-median stock."
              />
              {inventory.low_stock_high_sales.length > 0 ? (
                <Recommendation kind="understocked" />
              ) : null}
            </>
          ) : (
            loadingPanel
          )}
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="p-hd">
            <h3>Zero sales SKUs</h3>
            {inventory && inventory.zero_sales_total > inventory.zero_sales.length ? (
              <span className="hint">
                {n(inventory.zero_sales_total)} in total · largest {inventory.zero_sales.length}{' '}
                shown
              </span>
            ) : null}
          </div>
          {inventory ? (
            <>
              <RankingTable
                rows={inventory.zero_sales}
                measure="stock"
                emptyNote="Every SKU holding stock has sold at least one unit."
              />
              {inventory.zero_sales.length > 0 ? <Recommendation kind="dead" /> : null}
            </>
          ) : (
            loadingPanel
          )}
        </div>

        <div className="panel">
          <div className="p-hd">
            <h3>Highest complaint SKUs</h3>
            <span className="hint">By total complaints</span>
          </div>
          {inventory ? (
            <>
              <RankingTable
                rows={inventory.most_complaints}
                measure="complaints"
                emptyNote="No SKU with orders has a complaint against it."
              />
              {inventory.most_complaints.length > 0 ? (
                <Recommendation kind="complaints" />
              ) : null}
            </>
          ) : (
            loadingPanel
          )}
        </div>
      </div>

      {inventory && inventory.zero_sales_total === 0 && inventory.zero_sales.length === 0 ? (
        <div className="panel">
          <Empty note="Nothing needs attention: every SKU holding stock is selling." />
        </div>
      ) : null}
    </AnalyticsFrame>
  );
}
