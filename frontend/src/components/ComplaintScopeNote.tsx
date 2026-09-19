/**
 * Whether the complaint figures beside this note answer to the date range.
 *
 * Two upload formats reach this system. A complaint export carries a date on
 * every row, so its complaints are filtered to the selected window like the
 * Shopify figures are. An aggregated sheet carries no date column at all, so its
 * totals are the same in every range — and without being told, a reader would
 * take "12 complaints" over the last 30 days as a 30-day figure when it is the
 * whole tally.
 *
 * **A workspace can be either, or both**, because each import replaces only the
 * SKUs it names. Three states, three sentences:
 *
 * | | |
 * |---|---|
 * | every SKU dated | the range applies; say so plainly |
 * | no SKU dated | the range does not apply to complaints at all |
 * | some of each | name how much is not filtered, so the figure can be read |
 *
 * One earlier version said *"…because no Complaint Date column was provided"*
 * in all three cases. On a workspace with 308 dated SKUs and 883 undated ones
 * that reads as "your dates were ignored", which sent a real user looking for a
 * bug in the importer that was not there. The mixed case is the common one once
 * a store has imported twice, so it gets its own sentence rather than a caveat
 * bolted onto the wrong one.
 *
 * The wording lives here, not on the server: the counts need `n()` so they group
 * the way every other figure in this app does, and one component is already the
 * single render point for the Dashboard, SKU Performance and the Complaints lens.
 */

import type { ComplaintScope } from '../types/api';
import { day, n } from '../lib/format';

/**
 * A count and its noun, agreeing.
 *
 * The live workspace sat at one undated SKU with one complaint on it, so the
 * note read "1 SKUs (1 complaints)". Both nouns here are regular, so an `s` is
 * enough — anything irregular should be passed in whole rather than guessed at.
 */
function count(value: number, noun: string): string {
  return `${n(value)} ${noun}${value === 1 ? '' : 's'}`;
}

/**
 * How far the dated complaint record reaches, and whether the selected range
 * has run past the end of it.
 *
 * Only rendered where complaints actually follow the range. Where they do not,
 * a sentence about which days the dated records cover explains nothing about
 * the figure on screen and would read as a caveat on a number it does not
 * apply to.
 *
 * The warning is the point. A window beginning after the last dated complaint
 * returns zero from records that exist — identical on screen to a period that
 * genuinely had none, and the opposite thing to act on.
 */
function Freshness({ through, since }: { through: string | null | undefined; since?: string }) {
  const readable = day(through);
  if (!readable || !through) return null;

  // Both are `yyyy-mm-dd`, which sorts lexicographically as it sorts by date.
  const rangeStartsAfter = since !== undefined && since > through;

  return (
    <>
      {' '}
      Dated complaints run through <b>{readable}</b>
      {rangeStartsAfter ? '; a range after that date shows none.' : '.'}
    </>
  );
}

export function ComplaintScopeNote({
  scope,
  since,
}: {
  scope: ComplaintScope | undefined;
  /** First day of the selected range, `yyyy-mm-dd`. Omit where none applies. */
  since?: string;
}) {
  if (!scope) return null;

  const { dated_skus: dated, undated_skus: undated, undated_complaints: complaints } = scope;

  // Nothing imported, or nothing with a complaint on it. There is no question
  // to answer, so there is nothing to say.
  if (dated === 0 && undated === 0) return null;

  if (undated === 0) {
    return (
      <div className="trend-scope" role="note">
        Complaint totals follow the selected date range.
        <Freshness through={scope.dated_through} since={since} />
      </div>
    );
  }

  // Both remaining branches are `dated === 0`. This one is the narrower case
  // and so is tested first: dated records exist, and this page asked for the
  // whole record anyway.
  //
  // **The two are not the same thing and must not share a sentence.** "No SKU
  // here is dated" and "this page is not using the dates" both arrive as
  // `dated_skus: 0`, and only `dated_through` tells them apart. Without this
  // branch, SKU Performance would tell a workspace holding 253 dated SKUs that
  // its file has no Complaint Date column — the exact wording that once sent
  // someone looking for an importer bug that did not exist.
  const through = day(scope.dated_through);
  if (dated === 0 && through !== null) {
    return (
      <div className="trend-scope" role="note">
        Complaint totals are the full record for every SKU and do not follow the date range.
        Dated complaints run through <b>{through}</b>.
      </div>
    );
  }

  if (dated === 0) {
    return (
      <div className="trend-scope" role="note">
        Complaint totals are not filtered by date because the imported file does not contain a
        Complaint Date column.
      </div>
    );
  }

  return (
    <div className="trend-scope" role="note">
      Some imported complaint records follow the selected date range. The remaining{' '}
      <b>{count(undated, 'SKU')}</b> ({count(complaints, 'complaint')}) were imported without
      Complaint Dates, so their complaint totals are not date-filtered.
      <Freshness through={scope.dated_through} since={since} />
    </div>
  );
}
