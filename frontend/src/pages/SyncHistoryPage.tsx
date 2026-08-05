import { Fragment, useCallback, useEffect, useState } from 'react';

import { Icon } from '../components/Icon';
import { Skeleton } from '../components/Skeleton';
import { SyncResultBadge } from '../components/StatusBadge';
import { SyncStepsToggle } from '../components/SyncSteps';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { useShopifyStatus } from '../hooks/useShopifyStatus';
import { StockSyncApiError, api } from '../lib/api';
import { n } from '../lib/format';
import type { SyncHistoryPage as HistoryPage, SyncResult } from '../types/api';

type Filter = 'all' | SyncResult;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'success', label: 'Success' },
  { key: 'partial', label: 'Partial' },
  { key: 'failed', label: 'Failed' },
];

const TRIGGERS: Record<string, string> = {
  manual: 'Manual',
  scheduled: 'Scheduled',
};

/** Design doc §9.1 — every pull from Shopify, with what came back. */
export function SyncHistoryPage() {
  const [filter, setFilter] = useState<Filter>('all');
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The shell's sync, so this page, the header pill and the dashboard all
  // watch one run rather than three pollers of the same endpoint.
  const { sync } = useShopifyStatus();

  const load = useCallback(async (which: Filter) => {
    setPage(null);
    setError(null);
    try {
      const query = which === 'all' ? '' : `?result=${which}`;
      setPage(await api.get<HistoryPage>(`/shopify/syncs${query}`));
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError
          ? caught.message
          : 'Could not load your sync history.',
      );
    }
  }, []);

  useEffect(() => {
    void load(filter);
    // completedAt changes when a sync finishes, so the table refreshes without
    // the user reloading the page.
  }, [filter, load, sync.completedAt]);

  return (
    <Page>
      <PageHeader
        title="Sync history"
        subtitle="Every pull from Shopify, with what came back"
      />

      <div className="panel">
        <div className="p-hd">
          <h3>All syncs</h3>
          <div className="r">
            <div className="seg">
              {FILTERS.map((option) => (
                <button
                  key={option.key}
                  className={option.key === filter ? 'on' : ''}
                  onClick={() => setFilter(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error ? (
          <div className="p-bd">
            <div className="inline-err">
              <Icon name="warn" />
              <div>{error}</div>
              <button className="btn sm" onClick={() => void load(filter)}>
                Retry
              </button>
            </div>
          </div>
        ) : page === null ? (
          <div className="p-bd" aria-busy="true">
            {[0, 1, 2].map((row) => (
              <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
            ))}
          </div>
        ) : page.items.length === 0 ? (
          <div className="empty">
            <div className="ei">
              <Icon name="sync" size="l" />
            </div>
            <h3>No syncs yet</h3>
            <p>Sync your store to pull orders from Shopify and match them to your SKUs.</p>
          </div>
        ) : (
          <>
            <div className="tbl-scroll">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Trigger</th>
                    <th className="n">Line items</th>
                    <th className="n">Orders</th>
                    <th>Result</th>
                    <th className="n">Duration</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((run) => (
                    <Fragment key={run.id}>
                      <tr className={run.result === 'failed' ? 's-bad' : undefined}>
                        <td className="hero" data-l="Date">
                          {new Date(run.started_at).toLocaleString('en-IN', {
                            day: '2-digit',
                            month: 'short',
                            year: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </td>
                        <td data-l="Trigger">{TRIGGERS[run.trigger] ?? run.trigger}</td>
                        <td className="n" data-l="Line items">
                          {n(run.line_items_synced)}
                        </td>
                        <td className="n" data-l="Orders">
                          {n(run.orders_synced)}
                        </td>
                        <td data-l="Result">
                          <SyncResultBadge result={run.result} running={run.is_running} />
                        </td>
                        <td className="n" data-l="Duration">
                          {run.duration_ms === null
                            ? '—'
                            : `${(run.duration_ms / 1000).toFixed(1)}s`}
                        </td>
                        <td data-l="Detail">
                          {run.error_detail ? (
                            <span style={{ color: 'var(--rust)' }}>{run.error_detail}</span>
                          ) : (
                            <span style={{ color: 'var(--ink-45)' }}>—</span>
                          )}
                        </td>
                      </tr>
                      {/* Every automatic step of this run, on demand. A partial
                        result is one error string on its own; the steps say
                        which stage reached it. */}
                      <tr>
                        <td colSpan={7} style={{ paddingTop: 0 }}>
                          <SyncStepsToggle runId={run.id} />
                        </td>
                      </tr>
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="tbl-ft">
              <span>Partial syncs re-fetch only the missing pages on the next run.</span>
            </div>
          </>
        )}
      </div>
    </Page>
  );
}
