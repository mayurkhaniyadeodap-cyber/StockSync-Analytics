import { useCallback, useEffect, useRef, useState } from 'react';

import { StockSyncApiError, api } from '../lib/api';
import type { SyncState } from '../types/api';

/** Slow enough not to hammer the API, fast enough that a bar looks alive. */
const POLL_MS = 1500;

export interface UseSync {
  state: SyncState | null;
  starting: boolean;
  error: string | null;
  start: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Increments when a sync finishes — panels watch it to reload their data. */
  completedAt: number;
}

/**
 * Sync state, polled only while a sync is actually running.
 *
 * Polling is started and stopped by the server's `running` flag rather than by
 * the client guessing from the stage, so there is one definition of in-flight
 * and the page cannot keep polling a sync that finished.
 */
export function useSync(enabled: boolean): UseSync {
  const [state, setState] = useState<SyncState | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completedAt, setCompletedAt] = useState(0);

  // Tracked in a ref so the poll effect doesn't re-run on every state change.
  const wasRunning = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api.get<SyncState>('/shopify/sync');
      setState(next);
      if (wasRunning.current && !next.running) {
        // Finished since the last poll: tell dependent panels to reload.
        setCompletedAt(Date.now());
      }
      wasRunning.current = next.running;
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError ? caught.message : 'Could not load sync state.',
      );
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
  }, [enabled, refresh]);

  useEffect(() => {
    if (!enabled || !state?.running) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [enabled, state?.running, refresh]);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    try {
      const next = await api.post<SyncState>('/shopify/sync');
      setState(next);
      wasRunning.current = next.running;
      if (!next.running) setCompletedAt(Date.now());
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError ? caught.message : 'Could not start the sync.',
      );
    } finally {
      setStarting(false);
    }
  }, []);

  return { state, starting, error, start, refresh, completedAt };
}
