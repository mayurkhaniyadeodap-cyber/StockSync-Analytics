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
