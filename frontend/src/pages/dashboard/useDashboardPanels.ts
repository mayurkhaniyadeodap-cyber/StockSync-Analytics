/**
 * The three reads the dashboard's new panels need, beside `/analytics/overview`.
 *
 * All three are existing endpoints queried with existing parameters — nothing
 * was added to the API for this page, and nothing here derives a figure the
 * server does not already stand behind.
 *
 * | Panel | Read |
 * |---|---|
 * | Complaint breakdown | `/analytics/insights` — its `complaints.categories`, the same list Analytics draws its mix from |
 * | Inventory health | `/analytics/performance` twice — `max_qty=0` counts the SKUs holding nothing, and `min_qty=<threshold+1>&max_sales=0` counts the stocked ones that never sold. The other two buckets fall out of the KPI payload the page already has |
 * | Requiring attention | `/analytics/performance?sort=status`, worst first — the server's own verdict, not a rule re-implemented here |
 *
 * Settled independently rather than in one `Promise.all`: these are three
 * unrelated panels, and one endpoint failing should cost its own panel, not the
 * other two.
 */

import { useCallback, useEffect, useState } from 'react';

import { StockSyncApiError, api } from '../../lib/api';
import type { AnalyticsInsights, PerformancePage, PerformanceRow } from '../../types/api';

/** How many rows the attention table shows before pointing at the full page. */
export const ATTENTION_ROWS = 8;

/**
 * The window the stock split is read over — every row the rollup holds.
 *
 * Inventory Health is a snapshot panel: "no stock" and "low stock" come from
 * the newest import and never move, so deciding "has this SKU ever sold?" from
 * the selected range made two of the four slices swing while the other two sat
 * still — the same SKU counted as dead stock on a 7-day view and healthy on a
 * 90-day one.
 *
 * 365 is the endpoint's own maximum and comfortably exceeds what the rollup can
 * hold: `sku_daily_metrics` only ever covers the store's `order_lookback_days`.
 * So this window contains every row there is, without a new endpoint or an
 * unbounded query.
 */
const ALL_TIME_DAYS = 365;

/** The two verdicts that mean "look at this". `good` and `excellent` do not. */
const NEEDS_ATTENTION = new Set(['critical', 'attention']);

/**
 * The stock split, as four buckets that do not overlap.
 *
 * A partition, not four interesting facts: the donut shows them as shares of
 * one whole, so a SKU counted in two of them would make the shares sum past
 * 100%. The cuts are therefore on disjoint ranges of the same column —
 * `total_qty` ≤ 0, then up to the threshold, then above it split by whether it
 * has sold anything.
 *
 * Null means "not known", which is not the same as zero and is rendered
 * differently.
 */
export interface StockSplit {
  outOfStock: number | null;
  stockedNoSales: number | null;
}

export interface DashboardPanels {
  insights: AnalyticsInsights | null;
  insightsError: string | null;
  stock: StockSplit;
  attention: PerformanceRow[] | null;
  attentionError: string | null;
  reload: () => void;
}

function message(caught: unknown, fallback: string): string {
  return caught instanceof StockSyncApiError ? caught.message : fallback;
}

export function useDashboardPanels(
  days: number,
  changedAt: number,
  lowStockThreshold: number | undefined,
): DashboardPanels {
  const [insights, setInsights] = useState<AnalyticsInsights | null>(null);
  const [insightsError, setInsightsError] = useState<string | null>(null);
  const [stock, setStock] = useState<StockSplit>({ outOfStock: null, stockedNoSales: null });
  const [attention, setAttention] = useState<PerformanceRow[] | null>(null);
  const [attentionError, setAttentionError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const load = useCallback(async () => {
    const window = String(days);

    void (async () => {
      setInsightsError(null);
      try {
        // `complaints=total` for the same reason the page's other reads send
        // it: on this page complaints are the sheet's whole record, not a
        // slice of the range. See `COMPLAINT_BASIS` in DashboardPage.
        setInsights(
          await api.get<AnalyticsInsights>(
            `/analytics/insights?days=${window}&complaints=total`,
          ),
        );
      } catch (caught) {
        setInsightsError(message(caught, 'Could not load the complaint breakdown.'));
      }
    })();

    void (async () => {
      setAttentionError(null);
      try {
        const params = new URLSearchParams({
          days: window,
          // Ascending on status is worst-first — the server orders the four
          // verdicts critical, attention, good, excellent.
          complaints: 'total',
          sort: 'status',
          descending: 'false',
          limit: String(ATTENTION_ROWS),
        });
        const page = await api.get<PerformancePage>(
          `/analytics/performance?${params.toString()}`,
        );
        // Filtered on the client because `status` takes one verdict and this
        // table wants two. The sort has already brought them to the front, so
        // what is dropped here is the tail of a healthy workspace.
        setAttention(page.rows.filter((row) => NEEDS_ATTENTION.has(row.status)));
      } catch (caught) {
        setAttentionError(message(caught, 'Could not load the attention list.'));
      }
    })();
  }, [days]);

  /*
   * Refreshing does **not** clear what is on screen first.
   *
   * It used to `setInsights(null)` and `setAttention(null)` here, which put
   * every panel back to its skeleton each time a sync finished or the range
   * changed — figures that were already correct vanished for as long as the
   * request took, and on a slow response the page read as though it had never
   * loaded. The panels now hold the previous answer until a new one arrives.
   * The first load still shows skeletons, because the state starts null.
   */
  useEffect(() => {
    void load();
  }, [load, changedAt, nonce]);

  /**
   * The stock split, on its own effect.
   *
   * It is the one read that needs `lowStockThreshold`, which arrives a beat
   * after the page mounts because it rides on the KPI payload. Folded into
   * `load` above, that second render re-fetched the insights and the attention
   * list too — two requests the answer had not changed for.
   *
   * It waits for the threshold rather than guessing one: "stocked" is defined
   * against it, and a different cut here from the one the low-stock figure
   * beside it used would make the four buckets stop being a partition.
   */
  useEffect(() => {
    if (lowStockThreshold === undefined) return;
    let current = true;

    const count = async (filter: string): Promise<number | null> => {
      try {
        // `limit=1` because only the count is wanted: the row that comes back
        // is discarded, and asking for fifty to throw away forty-nine would
        // make a count cost a page.
        const page = await api.get<PerformancePage>(
          `/analytics/performance?days=${String(ALL_TIME_DAYS)}&${filter}&limit=1`,
        );
        return page.total;
      } catch {
        return null;
      }
    };

    void (async () => {
      const [outOfStock, stockedNoSales] = await Promise.all([
        count('max_qty=0'),
        count(`min_qty=${String(lowStockThreshold + 1)}&max_sales=0`),
      ]);
      if (current) setStock({ outOfStock, stockedNoSales });
    })();

    return () => {
      current = false;
    };
    // `days` is deliberately not a dependency: neither bucket moves with the
    // selected range any more.
  }, [lowStockThreshold, changedAt, nonce]);

  return {
    insights,
    insightsError,
    stock,
    attention,
    attentionError,
    reload: useCallback(() => setNonce((value) => value + 1), []),
  };
}
