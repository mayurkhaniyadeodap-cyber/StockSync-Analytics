import { Fragment, useCallback, useEffect, useState } from 'react';

import { Icon } from '../components/Icon';
import { Pager } from '../components/Pager';
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
  // A sync that stopped on its time limit queues the next chunk itself. Named
  // so a run of these reads as one long sync in pieces rather than as the app
  // syncing over and over for no reason.
  continuation: 'Continued',
};

/** Rows per request. The endpoint's own default, stated here so the pager and
    the query cannot drift apart. */
const PAGE_SIZE = 50;

/** Design doc §9.1 — every pull from Shopify, with what came back. */
export function SyncHistoryPage() {
  const [filter, setFilter] = useState<Filter>('all');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The shell's sync, so this page, the header pill and the dashboard all
  // watch one run rather than three pollers of the same endpoint.
  const { sync } = useShopifyStatus();

  const load = useCallback(async (which: Filter, from: number) => {
    setPage(null);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(from) });
      if (which !== 'all') params.set('result', which);
      setPage(await api.get<HistoryPage>(`/shopify/syncs?${params.toString()}`));
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError
          ? caught.message
          : 'Could not load your sync history.',
      );
    }
  }, []);

  useEffect(() => {
    void load(filter, offset);
    // completedAt changes when a sync finishes, so the table refreshes without
    // the user reloading the page.
  }, [filter, load, offset, sync.completedAt]);

  // A filter narrows the set, so page four of the old one is very likely past
  // the end of the new one — and an empty table with a pager reading "page 4"
  // looks like a bug rather than a filter.
  const changeFilter = (next: Filter) => {
    setOffset(0);
    setFilter(next);
  };

  return (
    <Page>
      <PageHeader
        title="Sync history"
        subtitle="Every pull from Shopify, with what came back"
      />

      <div className="panel">
        <div className="p-hd">
          <span className="p-chip" aria-hidden="true">
            <Icon name="sync" size="s" />
          </span>
          <h3>All syncs</h3>
          <div className="r">
            <div className="seg">
              {FILTERS.map((option) => (
                <button
                  key={option.key}
                  className={option.key === filter ? 'on' : ''}
                  onClick={() => changeFilter(option.key)}
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
              <button className="btn sm" onClick={() => void load(filter, offset)}>
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
            <Pager
              offset={page.offset}
              limit={page.limit}
              total={page.total}
              onGo={setOffset}
              unit={page.total === 1 ? 'sync' : 'syncs'}
            />
          </>
        )}
      </div>
    </Page>
  );
}
