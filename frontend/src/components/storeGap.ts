/**
 * How far behind the live store the synced orders are.
 *
 * Every sync ends by asking Shopify for its newest order and writing the answer
 * onto the connection, so `store_latest_order_at` and `freshness_checked_at`
 * are already in the `/shopify/connection` payload every screen holds. Nothing
 * was reading them — the application measured the gap continuously and never
 * said what it was.
 *
 * Reading the stored pair rather than calling `/shopify/freshness` is the whole
 * point: that endpoint costs a live Shopify request, and putting one in the
 * path of a dashboard load would put the store's rate limit there too.
 *
 * Three answers, and they are genuinely different:
 *
 * * `unknown` — never measured, or the check could not reach Shopify. **Not**
 *   the same as "current", and reporting the second when the first is true is
 *   how a stale figure gets presented as a fresh one.
 * * `current` — within tolerance. A sync takes minutes and orders arrive
 *   continuously, so a small gap is the steady state, not a fault.
 * * `behind` — there are orders in Shopify this workspace has not pulled.
 */

import { freshness } from '../lib/format';

/** Matches the server's own tolerance in `sync.check_freshness`. */
const TOLERANCE_MINUTES = 15;

export type StoreGapState = 'unknown' | 'current' | 'behind';

export interface StoreGap {
  state: StoreGapState;
  /** One line for the card. */
  label: string;
  /** When the gap was last measured, or null when it never was. */
  checkedAt: Date | null;
}

export function storeGap(
  storeLatestOrderAt: string | null | undefined,
  syncedThrough: string | null | undefined,
  checkedAtIso: string | null | undefined,
  now: Date = new Date(),
): StoreGap {
  const checkedAt = checkedAtIso ? new Date(checkedAtIso) : null;

  if (!storeLatestOrderAt || !checkedAt) {
    return { state: 'unknown', label: 'Not measured yet', checkedAt };
  }

  const storeLatest = new Date(storeLatestOrderAt);
  const ours = syncedThrough ? new Date(syncedThrough) : null;

  if (!ours) {
    return {
      state: 'behind',
      label: `Store has orders to ${freshness(storeLatest, now)}; none pulled yet`,
      checkedAt,
    };
  }

  const minutes = Math.floor((storeLatest.getTime() - ours.getTime()) / 60_000);

  if (minutes <= TOLERANCE_MINUTES) {
    return { state: 'current', label: 'Up to date with Shopify', checkedAt };
  }

  return {
    state: 'behind',
    // `freshness(since, now)` measures `now - since`, so the pulled-through
    // point is the *since* and the store's newest order is the *now*. Reversed,
    // every real gap came out negative and read "just now" — which is the one
    // answer this must never give when the store is ahead.
    label: `Shopify has orders ${freshness(ours, storeLatest)} newer than the last pull`,
    checkedAt,
  };
}
