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
import { useShopifyStatus } from '../../hooks/useShopifyStatus';
import { StockSyncApiError, api } from '../../lib/api';
import type { AnalyticsInsights, RebuildResult } from '../../types/api';

/** Survives navigation between the Analytics pages, but not a reload. */
let rememberedRange: Range = 30;

export interface InsightsState {
  insights: AnalyticsInsights | null;
  error: string | null;
  loading: boolean;
  range: Range;
  setRange: (days: Range) => void;
  reload: () => Promise<void>;
  rebuild: () => Promise<RebuildResult>;
  rebuilding: boolean;
}

export function useInsights(): InsightsState {
  // The same signal the dashboard watches. `/analytics/insights` carries
  // `syncing`, which draws the "Sync in progress…" banner on every Analytics
  // page — so without this the banner had nothing to clear it and stayed up
  // until a manual reload, exactly as it did on the dashboard. It stays 0
  // through the first load, so arriving on a page does not fetch twice.
  const { changedAt } = useShopifyStatus();
  const [range, setRangeState] = useState<Range>(rememberedRange);
  const [insights, setInsights] = useState<AnalyticsInsights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  const load = useCallback(async (days: number) => {
    setError(null);
    try {
      setInsights(await api.get<AnalyticsInsights>(`/analytics/insights?days=${String(days)}`));
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError ? caught.message : 'Could not load your analytics.',
      );
    }
  }, []);

  useEffect(() => {
    void load(range);
  }, [load, range, changedAt]);

  const setRange = useCallback((days: Range) => {
    rememberedRange = days;
    setRangeState(days);
  }, []);

  const reload = useCallback(() => load(range), [load, range]);

  const rebuild = useCallback(async () => {
    setRebuilding(true);
    try {
      const result = await api.post<RebuildResult>('/analytics/rebuild');
      await load(range);
      return result;
    } finally {
      setRebuilding(false);
    }
  }, [load, range]);

  return {
    insights,
    error,
    loading: insights === null && error === null,
    range,
    setRange,
    reload,
    rebuild,
    rebuilding,
  };
}
