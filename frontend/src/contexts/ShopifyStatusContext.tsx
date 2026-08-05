/**
 * One live picture of the Shopify integration, shared by everything that shows it.
 *
 * The header's notification list, the dashboard's Shopify panel and the sync
 * pill all describe the same thing. Fetched once here rather than three times:
 * mounting three copies of the same three requests would be the "duplicate API
 * calls" problem, and worse, they could disagree — one panel saying "connected"
 * while another still showed the previous state.
 *
 * **No endpoint was added for this.** Everything comes from
 * `/shopify/connection`, `/shopify/sync` and `/shopify/sales/summary`, which
 * already existed and are already what the Shopify page reads.
 *
 * `store_latest_order_at` on the connection is what lets "new orders are
 * waiting" be answered without a live Shopify call: every sync records it, and
 * the Shopify page's freshness check refreshes it. A dashboard load spends no
 * Shopify request.
 */

import { createContext, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { useSync } from '../hooks/useSync';
import type { UseSync } from '../hooks/useSync';
import { api } from '../lib/api';
import type { ConnectionState, SalesSummary } from '../types/api';

/** One thing worth telling the user, in the terms they would say it. */
export interface Notice {
  key: string;
  tone: 'moss' | 'amber' | 'rust' | 'slate';
  title: string;
  detail: string;
  /** Where clicking it should go. */
  to: string;
}

export interface ShopifyStatus {
  connection: ConnectionState | null;
  summary: SalesSummary | null;
  sync: UseSync;
  /** True until the first connection response lands — not "not connected". */
  loading: boolean;
  notices: Notice[];
  /** Re-read the store, and announce the change through `changedAt`. */
  reload: () => Promise<void>;
  /**
   * Increments whenever the store changes — connect, disconnect, verify, or a
   * sync finishing. Screens whose own data depends on Shopify watch this to
   * reload themselves; it stays 0 through the first load so arriving on a page
   * does not fetch everything twice.
   */
  changedAt: number;
}

export const ShopifyStatusContext = createContext<ShopifyStatus | null>(null);

export function ShopifyStatusProvider({ children }: { children: ReactNode }) {
  const [connection, setConnection] = useState<ConnectionState | null>(null);
  const [summary, setSummary] = useState<SalesSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [changedAt, setChangedAt] = useState(0);
  // Polls only while a sync is running; the server's `running` flag stops it.
  const sync = useSync(true);

  const load = useCallback(async () => {
    try {
      const [nextConnection, nextSummary] = await Promise.all([
        api.get<ConnectionState>('/shopify/connection'),
        api.get<SalesSummary>('/shopify/sales/summary'),
      ]);
      setConnection(nextConnection);
      setSummary(nextSummary);
    } catch {
      // A failure here must not blank the screen: the panels render a "couldn't
      // read this" state from `connection === null`, and the previous good
      // values are better than nothing while a retry is possible.
    } finally {
      setLoading(false);
    }
  }, []);

  // Separate from the mount load below, which must not announce a change: at
  // that point nothing has changed, and a dependent screen would fetch twice
  // just for arriving.
  const reload = useCallback(async () => {
    await load();
    setChangedAt((count) => count + 1);
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  // A finished sync changes every figure here, so the panels refresh without
  // the user reloading the page. `completedAt` is the server-confirmed
  // transition out of running, not a guess from a timer.
  useEffect(() => {
    if (sync.completedAt) void reload();
  }, [sync.completedAt, reload]);

  const notices = useMemo(
    () => buildNotices(connection, summary, sync, loading),
    [connection, summary, sync, loading],
  );

  const value = useMemo(
    () => ({ connection, summary, sync, loading, notices, reload, changedAt }),
    [connection, summary, sync, loading, notices, reload, changedAt],
  );

  return (
    <ShopifyStatusContext.Provider value={value}>{children}</ShopifyStatusContext.Provider>
  );
}

/**
 * What the system would tell you if you asked.
 *
 * Ordered by what needs doing first, and **empty is a real answer** — "you're
 * all caught up" is only said when every check actually passed, rather than as
 * the one thing this list has ever shown.
 */
function buildNotices(
  connection: ConnectionState | null,
  summary: SalesSummary | null,
  sync: UseSync,
  loading: boolean,
): Notice[] {
  if (loading || connection === null) return [];

  const notices: Notice[] = [];
  const row = connection.connection;

  if (!connection.connected || row === null) {
    // Nothing else is worth saying: every other notice is about a store.
    return [
      {
        key: 'not-connected',
        tone: 'amber',
        title: 'Shopify not connected',
        detail: 'Sales figures stay empty until a store is connected.',
        to: '/shopify',
      },
    ];
  }

  if (row.status === 'missing_scopes' || row.status === 'token_expired') {
    notices.push({
      key: 'credential',
      tone: 'rust',
      title:
        row.status === 'missing_scopes' ? 'Shopify scope missing' : 'Shopify token rejected',
      detail: 'The stored credential can no longer read orders.',
      to: '/shopify',
    });
  }

  if (sync.state?.running) {
    const run = sync.state.run;
    notices.push({
      key: 'running',
      tone: 'slate',
      title: 'Sync in progress',
      detail: run
        ? `${run.orders_synced.toLocaleString('en-IN')} orders so far`
        : 'Fetching orders from Shopify.',
      to: '/sync-history',
    });
  } else if (sync.state?.run) {
    const run = sync.state.run;
    if (run.result === 'success') {
      notices.push({
        key: 'last-sync',
        tone: 'moss',
        title: 'Last sync successful',
        detail: `${run.orders_synced.toLocaleString('en-IN')} orders synced.`,
        to: '/sync-history',
      });
    } else {
      notices.push({
        key: 'last-sync',
        tone: 'rust',
        title: run.result === 'partial' ? 'Last sync did not finish' : 'Last sync failed',
        // The server's own sentence, not one invented here (design §16).
        detail: run.error_detail ?? 'It stopped before completing.',
        to: '/sync-history',
      });
    }
  }

  // Recorded by the last sync and by the Shopify page's freshness check, so
  // this costs nothing and is still a real comparison against the live store.
  const storeLatest = row.store_latest_order_at;
  const ours = summary?.last_synced_at ?? null;
  if (storeLatest && ours && new Date(storeLatest) > new Date(ours)) {
    notices.push({
      key: 'new-orders',
      tone: 'amber',
      title: 'New Shopify orders available',
      detail: `The store has orders newer than the last sync.`,
      to: '/shopify',
    });
  }

  return notices;
}
