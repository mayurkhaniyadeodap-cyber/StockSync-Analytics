/**
 * Number and currency formatting.
 *
 * Ported from prototype/strata-prototype.html. Every figure in StockSync Analytics goes
 * through here, so the lakh/crore grouping and the ₹ presentation stay
 * identical across KPIs, tables, charts and exports.
 */

/** Indian digit grouping: 1,240 · 52,300 · 14,90,000. */
export function n(value: number): string {
  return value.toLocaleString('en-IN');
}

/**
 * Rupees, abbreviated the way Indian retail reads them.
 *
 *   946        → ₹946
 *   842_000    → ₹8.42 L
 *   84_200_000 → ₹8.42 Cr
 *
 * Abbreviation kicks in at a lakh because that is where the digit count stops
 * being scannable in a table cell.
 */
export function inr(value: number): string {
  if (value >= 10_000_000) return `₹${(value / 10_000_000).toFixed(2)} Cr`;
  if (value >= 100_000) return `₹${(value / 100_000).toFixed(2)} L`;
  return `₹${n(Math.round(value))}`;
}

/**
 * Two decimal places, always — 28.60%, not 28.6% or 29%.
 *
 * The one place a percentage becomes text in this application, matching the
 * server's `format_pct`. Two decimals rather than one because one loses real
 * distinctions at the scale these figures live at: a SKU accounting for 0.09%
 * of the store and one at 0.14% both render as 0.1%, which turns the head of
 * the Shopify Sales % ranking into a run of apparent ties.
 *
 * Formatting only. Sorting and filtering run on the server against the numeric
 * values, never against what this returns.
 */
export function pct(value: number): string {
  return `${value.toFixed(2)}%`;
}

/**
 * `part` as a percentage of `whole`, or 0 when there is no whole.
 *
 * The client-side twin of the server's `share_pct`, for the handful of shares a
 * chart derives from slices it already holds rather than asking for. Zero
 * rather than a division, so an empty chart renders `0.00%` instead of `NaN%`.
 *
 * Every percentage the server computes arrives already divided — do not
 * recompute those here, or the two will round differently.
 */
export function sharePct(part: number, whole: number): number {
  return whole ? (part / whole) * 100 : 0;
}

/**
 * Relative freshness label: "Synced 12 minutes ago" (doc §1.4).
 *
 * Every data-bearing screen carries one, so the user never has to guess how
 * current the numbers are.
 */
export function freshness(since: Date, now: Date = new Date()): string {
  const seconds = Math.floor((now.getTime() - since.getTime()) / 1000);

  if (seconds < 0) return 'just now';
  if (seconds < 60) return 'just now';

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;

  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}
