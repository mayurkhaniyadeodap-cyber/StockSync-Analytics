/**
 * "A sync is running" / "the figures are behind", above any page of figures.
 *
 * The Dashboard and every Analytics page draw the same two states from the same
 * two flags. It was the same markup twice, and drifted the way duplicated
 * markup does — the two copies had accumulated different comments explaining
 * the same rule, and the polling bug that stopped the banner clearing had to be
 * chased through both.
 *
 * **`syncing` wins, and the two are never both shown.** A sync commits its
 * orders page by page and recomputes the rollup at the end, so every run passes
 * through a window where orders exist that the rollup has not seen. Read as
 * staleness, that put "Sales figures are behind" on screen mid-sync — next to a
 * Retry the server would have refused, because a sync was already running.
 */

import { Icon } from './Icon';
import { RetrySyncButton } from './RetrySyncButton';

interface Props {
  /** A run is queued or in flight. Takes precedence over `stale`. */
  syncing: boolean | undefined;
  /** Orders arrived that the rollup never saw, and the recompute failed. */
  stale: boolean | undefined;
  /** Re-read the page's figures once a retry has been started. */
  onRetryStarted: () => void;
}

export function SyncStateNotice({ syncing, stale, onRetryStarted }: Props) {
  if (syncing) {
    return (
      <div style={{ marginBottom: 18 }}>
        {/* `role="status"`, not `alert`: work in progress is information. The
            slate variant carries the same meaning visually — rust would read
            as a problem. */}
        <div className="inline-err info" role="status" aria-busy="true">
          <Icon name="sync" />
          <div>
            <b>Sync in progress…</b> Shopify sales are being pulled and the figures recomputed.
            This page updates itself when it finishes.
          </div>
        </div>
      </div>
    );
  }

  if (stale) {
    return (
      <div style={{ marginBottom: 18 }}>
        {/* Only when the automatic recomputation actually failed. A sync now
            recomputes before it is called successful, so in the ordinary case
            this never appears — and when it does, the next sync retries on its
            own rather than waiting for the user to press anything. */}
        <div className="inline-err">
          <Icon name="warn" />
          <div>
            <b>Sales figures are behind the last sync.</b> The figures could not be recomputed
            from the orders that arrived. The orders themselves are already here, so this
            retries the recompute alone.
          </div>
          <RetrySyncButton onStarted={onRetryStarted} />
        </div>
      </div>
    );
  }

  return null;
}
