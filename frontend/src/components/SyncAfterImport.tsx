/**
 * The Shopify sync that follows an import, reported where the import ends.
 *
 * An import restates which SKUs matter; their sales are only as current as the
 * last pull from Shopify. That pull used to be a "Sync now" button, which meant
 * a freshly imported workspace showed stale sales — or none — until someone
 * thought to press it. It now starts on its own, and this is the only place
 * that says so, because the import screen is where the user already is.
 *
 * Four states, and the last one is the reason this is a component rather than a
 * toast: a failed sync must not read as a failed import. **The rows are in
 * either way.** Only the sales figures beside them are behind, and the way
 * forward is to try the sync again, not to upload the file again.
 */

import { useEffect, useState } from 'react';

import { Icon } from './Icon';
import { RetrySyncButton } from './RetrySyncButton';
import { useSync } from '../hooks/useSync';
import { n } from '../lib/format';
import type { SyncAfterImport as Payload, SyncState } from '../types/api';

export function SyncAfterImport({ sync }: { sync: Payload }) {
  // Polls only while a run is in flight; the server's `running` flag stops it.
  const state = useSync(sync.started);
  // Latched: once a run has been seen finishing, a later poll returning null
  // must not blank the outcome the user is reading.
  const [finished, setFinished] = useState<SyncState['run'] | null>(null);

  useEffect(() => {
    const run = state.state?.run;
    if (run && !run.is_running && run.result) setFinished(run);
  }, [state.state]);

  // Clears the latched outcome so the running state takes over again.
  const followRetry = async () => {
    setFinished(null);
    await state.refresh();
  };

  if (!sync.started) {
    // Not connected is not a problem to solve here — the sheet imported, and
    // there is simply no store to read sales from yet.
    if (sync.reason === 'not_connected') {
      return (
        <div className="trend-scope" role="status">
          <b>Shopify is not connected</b>, so there are no sales to match these SKUs against.
          Connect a store to see Shopify Sales and Shopify Sales %.
        </div>
      );
    }
    return (
      <div className="trend-scope" role="status">
        A Shopify sync was already running when this import finished. It covers these SKUs, so
        no second sync was started.
      </div>
    );
  }

  const running = Boolean(state.state?.running) || (!finished && !state.error);

  if (running) {
    return (
      <div className="trend-scope" role="status" aria-busy="true">
        <Icon name="sync" size="s" /> <b>Syncing Shopify sales…</b> The figures beside these
        SKUs will update when it finishes. You can leave this page — it keeps running.
      </div>
    );
  }

  if (finished?.result === 'failed') {
    return (
      <div className="trend-scope" role="alert">
        <b>Your import is saved.</b> The Shopify sync did not finish, so the sales figures
        beside these SKUs may be behind. {finished.error_detail}{' '}
        <RetrySyncButton onStarted={followRetry} running={running} />
      </div>
    );
  }

  if (finished?.result === 'partial') {
    // The orders landed and the recompute did not, or the pull stopped
    // part-way. Either way the sheet is in and the retry repeats only what
    // failed — there is nothing to upload again.
    return (
      <div className="trend-scope" role="alert">
        <b>Your import is saved.</b> {finished.error_detail ?? 'The sync did not finish.'}{' '}
        <RetrySyncButton onStarted={followRetry} running={running} />
      </div>
    );
  }

  return (
    <div className="trend-scope" role="status">
      <Icon name="check" size="s" /> <b>Shopify sales are up to date.</b>{' '}
      {n(finished?.orders_synced ?? 0)} orders synced — Shopify Sales, Shopify Sales % and every
      figure derived from them now reflect this import.
    </div>
  );
}
