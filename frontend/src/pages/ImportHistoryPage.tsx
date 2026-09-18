import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../components/Icon';
import { Pager } from '../components/Pager';
import { Skeleton } from '../components/Skeleton';
import { StatusBadge } from '../components/StatusBadge';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { StockSyncApiError, api } from '../lib/api';
import { n } from '../lib/format';
import { methodLabel } from '../lib/labels';
import type { ImportHistoryPage as HistoryPage } from '../types/api';

type Filter = 'all' | 'complete' | 'failed';

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'complete', label: 'Complete' },
  { key: 'failed', label: 'Failed' },
];

/** Rows per request. The endpoint's own default, stated here so the pager and
    the query cannot drift apart. */
const PAGE_SIZE = 50;

/** Design doc §8.8. */
export function ImportHistoryPage() {
  const navigate = useNavigate();

  const [filter, setFilter] = useState<Filter>('all');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (which: Filter, from: number) => {
    setPage(null);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(from) });
      if (which !== 'all') params.set('status', which);
      setPage(await api.get<HistoryPage>(`/imports?${params.toString()}`));
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError
          ? caught.message
          : 'Could not load your import history.',
      );
    }
  }, []);

  useEffect(() => {
    void load(filter, offset);
  }, [filter, load, offset]);

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
        title="Import history"
        subtitle="Every inventory load, and what came of it"
        actions={
          <button className="btn pri" onClick={() => navigate('/import')}>
            <Icon name="plus" size="s" /> New import
          </button>
        }
      />

      <div className="panel">
        <div className="p-hd">
          <span className="p-chip" aria-hidden="true">
            <Icon name="clock" size="s" />
          </span>
          <h3>All imports</h3>
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
              <Icon name="import" size="l" />
            </div>
            <h3>You haven’t imported any inventory yet.</h3>
            <p>
              Upload a stock sheet and it will show up here with what landed and what didn’t.
            </p>
            <div className="acts">
              <button className="btn cta" onClick={() => navigate('/import')}>
                Import now
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="tbl-scroll">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Date</th>
                    <th className="n">Total products</th>
                    <th className="n">Rows flagged</th>
                    <th>Status</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((batch) => (
                    <tr
                      key={batch.id}
                      className={batch.status === 'failed' ? 's-bad' : undefined}
                    >
                      <td className="hero" data-l="Source">
                        <div className="lt">
                          <b>{batch.origin_filename}</b>
                          <span className="sku">{methodLabel(batch.method)}</span>
                        </div>
                      </td>
                      <td data-l="Date">
                        {new Date(batch.started_at).toLocaleString('en-IN', {
                          day: '2-digit',
                          month: 'short',
                          year: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </td>
                      <td className="n" data-l="Total products">
                        {n(batch.rows_imported)}
                      </td>
                      <td className="n" data-l="Rows flagged">
                        {n(batch.rows_flagged)}
                      </td>
                      <td data-l="Status">
                        <StatusBadge status={batch.status} />
                      </td>
                      <td data-l="Detail">
                        {batch.error_detail ? (
                          <span style={{ color: 'var(--rust)' }}>{batch.error_detail}</span>
                        ) : batch.rows_rejected > 0 ? (
                          <span style={{ color: 'var(--ink-60)' }}>
                            {n(batch.rows_rejected)} rows rejected
                            {batch.rows_merged > 0 ? `, ${n(batch.rows_merged)} merged` : ''}
                          </span>
                        ) : batch.rows_merged > 0 ? (
                          <span style={{ color: 'var(--ink-60)' }}>
                            {n(batch.rows_merged)} duplicates merged
                          </span>
                        ) : (
                          <span style={{ color: 'var(--ink-45)' }}>—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager
              offset={page.offset}
              limit={page.limit}
              total={page.total}
              onGo={setOffset}
              unit={page.total === 1 ? 'import' : 'imports'}
            />
          </>
        )}
      </div>
    </Page>
  );
}
