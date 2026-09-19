/**
 * The period an analytics page is computed over, and how it reaches the server.
 *
 * A plain module rather than part of `DateRangeFilter.tsx` so the control can be
 * hot-reloaded on its own, and so the query shape can be read and tested without
 * rendering anything.
 */

export const RANGE_PRESETS = [30, 60, 90, 180] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number];

export type DateRange =
  { kind: 'preset'; days: RangePreset } | { kind: 'custom'; since: string; until: string };

export const DEFAULT_RANGE: DateRange = { kind: 'preset', days: 30 };

/**
 * The range as query parameters.
 *
 * A preset sends `days`; a custom pair sends `since`/`until`, which the server
 * prefers over `days` when both are present. Only ever one of the two shapes, so
 * neither the page nor the server has to reconcile two ideas of the period.
 */
export function rangeParams(range: DateRange): [string, string][] {
  if (range.kind === 'custom') {
    return [
      ['since', range.since],
      ['until', range.until],
    ];
  }
  return [['days', String(range.days)]];
}

/** How the current range reads in prose, for the subtitle and the scope note. */
export function rangeLabel(range: DateRange): string {
  return range.kind === 'custom'
    ? `${range.since} to ${range.until}`
    : `the last ${range.days} days`;
}

/** Today, as the `yyyy-mm-dd` an `<input type="date">` speaks. */
export function today(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/**
 * The first day the selected range covers, as `yyyy-mm-dd`.
 *
 * Mirrors `core.window.window_for` on the server: a preset of `days` is
 * inclusive of today, so it begins `days - 1` days back, not `days`. Kept
 * beside `rangeParams` because the two have to describe the same period — one
 * sends it, the other says what was sent.
 *
 * Client-side rather than read from the response because no payload carries the
 * resolved bounds; only `days` comes back. The two can differ by a day for a
 * reader whose clock is on the far side of midnight from the server's, which is
 * why nothing is *computed* from this — it decides whether to show a sentence.
 */
export function rangeStart(range: DateRange): string {
  return range.kind === 'custom' ? range.since : startOfTrailingDays(range.days);
}

/**
 * The first day of a trailing window of `days` ending today, as `yyyy-mm-dd`.
 *
 * Separate from `rangeStart` because the Dashboard holds its period as a bare
 * day count rather than a `DateRange`, and both screens have to agree about
 * which day the window opens on. One arithmetic, two ways in.
 */
export function startOfTrailingDays(days: number): string {
  const start = new Date(`${today()}T00:00:00`);
  start.setDate(start.getDate() - (days - 1));
  const local = new Date(start.getTime() - start.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}
