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

export interface ShopifyStatus {
  connection: ConnectionState | null;
  summary: SalesSummary | null;
  sync: UseSync;
  /** True until the first connection response lands — not "not connected". */
  loading: boolean;
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

  const value = useMemo(
    () => ({ connection, summary, sync, loading, reload, changedAt }),
    [connection, summary, sync, loading, reload, changedAt],
  );

  return (
    <ShopifyStatusContext.Provider value={value}>{children}</ShopifyStatusContext.Provider>
  );
}
