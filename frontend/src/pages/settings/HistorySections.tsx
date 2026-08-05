/**
 * Settings → Import history and Sync history.
 *
 * Previews, deliberately: a handful of recent rows and a way through to the
 * page that owns the subject. Filtering, paging and the full detail live there,
 * and duplicating them here would make two screens that have to agree about
 * what a sync is — which is exactly how they stop agreeing.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { StatusBadge, SyncResultBadge } from '../../components/StatusBadge';
import { StockSyncApiError, api } from '../../lib/api';
import { n } from '../../lib/format';
import { methodLabel } from '../../lib/labels';
import type { ImportHistoryPage, SyncHistoryPage } from '../../types/api';

/** Enough to see what has been happening, not enough to need paging. */
const PREVIEW = 5;

function when(value: string): string {
  return new Date(value).toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** The shared frame: heading, the states a fetch can be in, and the way out. */
function PreviewPanel({
  title,
  loading,
  error,
  empty,
  onRetry,
  onOpen,
  openLabel,
  children,
}: {
  title: string;
  loading: boolean;
  error: string | null;
  empty: boolean;
  onRetry: () => void;
  onOpen: () => void;
  openLabel: string;
  children: React.ReactNode;
}) {
  return (
    <div className="panel">
      <div className="p-hd">
        <h3>{title}</h3>
      </div>

      {error ? (
        <div className="p-bd">
          <div className="inline-err">
            <Icon name="warn" />
            <div>{error}</div>
            <button className="btn sm" onClick={onRetry}>
              <Icon name="refresh" size="s" /> Retry
            </button>
          </div>
        </div>
      ) : loading ? (
        <div className="p-bd" aria-busy="true">
          <Skeleton height={34} />
          <Skeleton height={34} style={{ marginTop: 8 }} />
          <Skeleton height={34} style={{ marginTop: 8 }} />
        </div>
      ) : empty ? (
        <div className="empty">
          <div className="ei">
            <Icon name="clock" size="l" />
          </div>
          <h3>Nothing yet</h3>
          <p>Once there is something to show, the most recent appears here.</p>
        </div>
      ) : (
        <div className="tbl-scroll">{children}</div>
      )}

      <div className="p-ft">
        <span className="spacer" />
        <button className="btn" onClick={onOpen}>
          {openLabel} <Icon name="right" size="s" />
        </button>
      </div>
    </div>
  );
}

export function ImportsSection() {
  const navigate = useNavigate();
  const [page, setPage] = useState<ImportHistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPage(await api.get<ImportHistoryPage>(`/imports?limit=${String(PREVIEW)}`));
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError ? caught.message : "Couldn't load import history.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <PreviewPanel
      title="Recent imports"
      loading={page === null && error === null}
      error={error}
      empty={page?.items.length === 0}
      onRetry={() => void load()}
      onOpen={() => void navigate('/import-history')}
      openLabel="Open full page"
    >
      <table className="tbl">
        <thead>
          <tr>
            <th>Method</th>
            <th>Date &amp; time</th>
            <th className="n">Rows imported</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {page?.items.map((batch) => (
            <tr key={batch.id}>
              <td data-l="Method">{methodLabel(batch.method)}</td>
              <td data-l="Date & time">{when(batch.started_at)}</td>
              <td className="n" data-l="Rows imported">
                {n(batch.rows_imported)}
              </td>
              <td data-l="Status">
                <StatusBadge status={batch.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </PreviewPanel>
  );
}

export function SyncsSection() {
  const navigate = useNavigate();
  const [page, setPage] = useState<SyncHistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPage(await api.get<SyncHistoryPage>(`/shopify/syncs?limit=${String(PREVIEW)}`));
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError ? caught.message : "Couldn't load sync history.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <PreviewPanel
      title="Recent syncs"
      loading={page === null && error === null}
      error={error}
      empty={page?.items.length === 0}
      onRetry={() => void load()}
      onOpen={() => void navigate('/sync-history')}
      openLabel="Open full page"
    >
      <table className="tbl">
        <thead>
          <tr>
            <th>Sync time</th>
            <th className="n">Orders synced</th>
            <th className="n">Line items</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {page?.items.map((run) => (
            <tr key={run.id}>
              <td data-l="Sync time">{when(run.started_at)}</td>
              <td className="n" data-l="Orders synced">
                {n(run.orders_synced)}
              </td>
              <td className="n" data-l="Line items">
                {n(run.line_items_synced)}
              </td>
              <td data-l="Status">
                <SyncResultBadge result={run.result} running={run.is_running} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </PreviewPanel>
  );
}
