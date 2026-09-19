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
 * Complaints as a percentage of orders, capped at 100, or `null` with nothing
 * to divide by.
 *
 * **The one implementation.** Two screens show this figure — the dashboard's
 * Complaint Rate card and the Rate column in Products Requiring Attention — and
 * they each derived it themselves, which is how one of them would eventually
 * cap and the other would not.
 *
 * The cap is a display decision, not a correction. The underlying ratio really
 * can exceed 100%, for two honest reasons: one order can produce several
 * complaints, and the date range moves the numerator while the denominator
 * stays a snapshot of the newest import. Neither is a defect in the data, but
 * "312.50%" reads as a broken number rather than a large one, so the reported
 * figure stops at 100%.
 *
 * `null` rather than 0 when there are no orders: no rate is not a rate of zero,
 * and the two callers say so differently — an em dash in a table cell, a
 * sentence on a card. Guarding here also keeps the division from ever running
 * against a zero denominator.
 */
export function complaintRate(complaints: number, orders: number): number | null {
  if (orders <= 0) return null;
  return Math.min(sharePct(complaints, orders), 100);
}

/**
 * A `yyyy-mm-dd` from the server as a day a reader can say out loud.
 *
 * Parsed with an explicit `T00:00:00` so it is read as local midnight. Handing
 * a bare `yyyy-mm-dd` to `new Date` parses it as UTC, which renders as the day
 * before anywhere west of Greenwich — the same trap the trend labels avoid.
 *
 * Returns null for anything unparseable rather than "Invalid Date", so a caller
 * can leave the sentence out instead of printing nonsense into it.
 */
export function day(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
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
