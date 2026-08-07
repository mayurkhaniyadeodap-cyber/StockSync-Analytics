import { useCallback, useEffect, useRef, useState } from 'react';

import { StockSyncApiError, api } from '../lib/api';
import type { SyncState } from '../types/api';

/** Slow enough not to hammer the API, fast enough that a bar looks alive. */
const POLL_MS = 1500;

/**
 * How often to look while nothing is known to be running.
 *
 * Polling used to start only once a run had already been observed, which meant
 * a hook that mounted against an idle server stopped looking and never started
 * again. Every sync this app begins without going through `start()` was
 * therefore invisible to it — and the important one does exactly that: the
 * server queues a sync itself after each successful import.
 *
 * The shared status in `ShopifyStatusContext` is the instance that mattered.
 * It mounts once with the app, typically before any sync exists, so it saw
 * `running: false`, stopped, and never published a completion. The dashboard's
 * "Sync in progress…" banner is drawn from the analytics payload and only
 * re-read when that context announces a change — so with no announcement the
 * banner stayed up until the page was reloaded by hand.
 */
const IDLE_POLL_MS = 5000;

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
 * Sync state, polled continuously while enabled — quickly during a run, slowly
 * between them.
 *
 * The server's `running` flag chooses the rate rather than whether to look at
 * all, so there is one definition of in-flight and no run can begin unobserved.
 * Watching only during a run was the earlier design, and it could not see the
 * runs it had not started — see `IDLE_POLL_MS`.
 *
 * `enabled` still means *never* look, which is what `SyncAfterImport` wants
 * before an import has produced a sync to follow.
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
      // Cleared on success so one dropped poll does not leave a stale error on
      // screen for as long as the page stays open — this now polls forever, so
      // a sticky error would be a permanent one.
      setError(null);
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

  // Always polling, at one of two rates: fast enough to animate a run, slow
  // enough between runs to cost nothing. The idle rate is what lets a sync
  // started anywhere else — an import, another tab — ever be noticed.
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(() => void refresh(), state?.running ? POLL_MS : IDLE_POLL_MS);
    return () => clearInterval(timer);
  }, [enabled, state?.running, refresh]);

  // A background tab's timers are throttled, so returning to one can mean
  // looking at a sync that finished minutes ago. Re-reading on focus makes the
  // wait for the idle tick invisible in the case the user is actually watching.
  useEffect(() => {
    if (!enabled) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [enabled, refresh]);

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
