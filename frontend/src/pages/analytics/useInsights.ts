/**
 * Loading the Analytics figures.
 *
 * Four of the five pages read from `/analytics/insights`, each using a different
 * slice of it. One hook rather than four copies of the same fetch, error and
 * range plumbing — and one place where the range is remembered, so moving between
 * Sales and Complaints does not silently reset the window you chose.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Range } from '../../components/charts/RangePicker';
import { useSharedRange } from '../../hooks/useSharedRange';
import { useShopifyStatus } from '../../hooks/useShopifyStatus';
import { StockSyncApiError, api } from '../../lib/api';
import type { AnalyticsInsights, RebuildResult } from '../../types/api';

/**
 * The window a page reads over, when it does not read over the chosen one.
 *
 * 365 is the endpoint's maximum and comfortably exceeds what the rollup can
 * hold — `sku_daily_metrics` only ever covers the store's `order_lookback_days`
 * — so it contains every row there is without a new endpoint or an unbounded
 * query. The same constant and the same reasoning as the Dashboard's stock
 * split.
 */
const ALL_TIME_DAYS = 365;

export interface InsightsOptions {
  /**
   * Whether complaint figures follow the selected range.
   *
   * `'total'` asks the server for the sheet's whole complaint record and pins
   * the request window, so the page becomes independent of the shared range
   * entirely: nothing it renders moves when the range changes elsewhere, and
   * it does not refetch for a change that cannot affect it.
   *
   * Complaint Analytics passes it. The other four pages do not — Shopify Sales
   * and Shopify Sales % are exactly what their range is for.
   */
  complaints?: 'range' | 'total';
}

export interface InsightsState {
  insights: AnalyticsInsights | null;
  error: string | null;
  loading: boolean;
  range: Range;
  setRange: (days: Range) => void;
  reload: () => Promise<void>;
  rebuild: () => Promise<RebuildResult>;
  rebuilding: boolean;
  /** False when this page's figures do not follow the range at all. */
  rangeApplies: boolean;
}

export function useInsights({ complaints = 'range' }: InsightsOptions = {}): InsightsState {
  // The same signal the dashboard watches. `/analytics/insights` carries
  // `syncing`, which draws the "Sync in progress…" banner on every Analytics
  // page — so without this the banner had nothing to clear it and stayed up
  // until a manual reload, exactly as it did on the dashboard. It stays 0
  // through the first load, so arriving on a page does not fetch twice.
  const { changedAt } = useShopifyStatus();
  /*
   * The window the header shows.
   *
   * This used to be a module-level `rememberedRange`, which already made the
   * range outlive a move between Analytics pages. The shared context does the
   * same thing and one more: the header's date control and this page's own
   * range control are now the same setting rather than two that can disagree
   * on screen.
   */
  const { days: range, setDays: setRange } = useSharedRange();

  /*
   * The window the *request* uses, which is not always the one the control
   * shows.
   *
   * A page reading the whole complaint record is pinned to the full window: the
   * shared range cannot move anything it renders, so letting it drive the fetch
   * would only refetch for a change with no effect. `range` stays the shared
   * value so its type stays honest and the control — on the pages that have one
   * — still binds to the real setting.
   */
  const requestDays = complaints === 'total' ? ALL_TIME_DAYS : range;
  const [insights, setInsights] = useState<AnalyticsInsights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  const load = useCallback(
    async (days: number) => {
      setError(null);
      try {
        setInsights(
          await api.get<AnalyticsInsights>(
            `/analytics/insights?days=${String(days)}&complaints=${complaints}`,
          ),
        );
      } catch (caught) {
        setError(
          caught instanceof StockSyncApiError
            ? caught.message
            : 'Could not load your analytics.',
        );
      }
    },
    [complaints],
  );

  useEffect(() => {
    void load(requestDays);
  }, [load, requestDays, changedAt]);

  const reload = useCallback(() => load(requestDays), [load, requestDays]);

  const rebuild = useCallback(async () => {
    setRebuilding(true);
    try {
      const result = await api.post<RebuildResult>('/analytics/rebuild');
      await load(requestDays);
      return result;
    } finally {
      setRebuilding(false);
    }
  }, [load, requestDays]);

  return {
    insights,
    error,
    loading: insights === null && error === null,
    range,
    setRange,
    reload,
    rebuild,
    rebuilding,
    rangeApplies: complaints !== 'total',
  };
}
