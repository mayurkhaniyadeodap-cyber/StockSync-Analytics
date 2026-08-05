/**
 * Retry whichever stage of the last sync failed.
 *
 * One component because a failed sync is visible in three places — the import
 * screen, the Dashboard's staleness banner and the Shopify page's status cell —
 * and a user who finds the failure in any of them should be able to act on it
 * there rather than having to remember where the button lives.
 *
 * **It never asks for the file again.** An import states which SKUs matter, and
 * that did not change because a sync failed. The server decides what to repeat:
 * a Shopify pull that stopped resumes from its cursor, and a pull that worked
 * with a recompute that did not skips Shopify entirely, because those orders are
 * already in the database.
 */

import { useState } from 'react';

import { Icon } from './Icon';
import { useToast } from '../hooks/useToast';
import { StockSyncApiError, api } from '../lib/api';
import type { SyncState } from '../types/api';

export function RetrySyncButton({
  onStarted,
  running = false,
  size = 'sm',
}: {
  /** Called once the retry is queued, so the caller can start following it. */
  onStarted?: () => void | Promise<void>;
  /** True while a sync is in flight. The button renders nothing. */
  running?: boolean;
  size?: 'sm' | 'md';
}) {
  const { toast } = useToast();
  const [retrying, setRetrying] = useState(false);

  // Nothing to retry while a run is already doing it. The server refuses a
  // second one with 409, so this is not the guard — it is what stops the user
  // being offered a button that cannot work. Passed in rather than read from a
  // context, so the button works anywhere and the knowledge stays with whoever
  // already has it.
  if (running) return null;

  const retry = async () => {
    setRetrying(true);
    try {
      await api.post<SyncState>('/shopify/sync/retry');
      await onStarted?.();
    } catch (caught) {
      toast(
        caught instanceof StockSyncApiError ? caught.message : 'Could not start the retry.',
        'rust',
        true,
      );
    } finally {
      setRetrying(false);
    }
  };

  return (
    <button
      className={size === 'sm' ? 'btn sm' : 'btn'}
      onClick={() => void retry()}
      disabled={retrying}
    >
      <Icon name="sync" size="s" /> {retrying ? 'Retrying…' : 'Retry sync'}
    </button>
  );
}
