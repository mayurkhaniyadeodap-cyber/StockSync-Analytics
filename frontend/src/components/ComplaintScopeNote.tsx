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
import { n } from '../lib/format';

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

export function ComplaintScopeNote({ scope }: { scope: ComplaintScope | undefined }) {
  if (!scope) return null;

  const { dated_skus: dated, undated_skus: undated, undated_complaints: complaints } = scope;

  // Nothing imported, or nothing with a complaint on it. There is no question
  // to answer, so there is nothing to say.
  if (dated === 0 && undated === 0) return null;

  if (undated === 0) {
    return (
      <div className="trend-scope" role="note">
        Complaint totals follow the selected date range.
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
    </div>
  );
}
