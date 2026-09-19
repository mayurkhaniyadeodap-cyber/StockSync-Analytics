// @vitest-environment jsdom
/**
 * The three things this note can say, and the one time it says nothing.
 *
 * Worth its own file because the component now owns the wording, and because
 * the mixed case is the one that went wrong in production: a workspace with 308
 * dated SKUs and 883 undated ones was told "no Complaint Date column was
 * provided", which reads as "your dates were ignored" and sent someone looking
 * for an importer bug that did not exist.
 */

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ComplaintScopeNote } from './ComplaintScopeNote';
import type { ComplaintScope } from '../types/api';

afterEach(cleanup);

const scope = (over: Partial<ComplaintScope>): ComplaintScope => ({
  filtered_by_date: false,
  dated_skus: 0,
  undated_skus: 0,
  undated_complaints: 0,
  ...over,
});

/** The note's text, or null when nothing rendered. */
function noteText(value: ComplaintScope | undefined): string | null {
  render(<ComplaintScopeNote scope={value} />);
  return screen.queryByRole('note')?.textContent ?? null;
}

describe('every SKU dated', () => {
  it('confirms the range reaches the complaint figures', () => {
    const text = noteText(scope({ filtered_by_date: true, dated_skus: 12 }));

    expect(text).toBe('Complaint totals follow the selected date range.');
  });

  it('says it even when this window happens to hold none', () => {
    // Zero complaints in range is a real answer, and the reader still needs to
    // know the range is what produced it.
    const text = noteText(scope({ filtered_by_date: true, dated_skus: 12 }));

    expect(text).toContain('follow the selected date range');
  });
});

describe('no SKU dated', () => {
  it('says the file had no Complaint Date column', () => {
    const text = noteText(scope({ undated_skus: 4, undated_complaints: 33 }));

    expect(text).toBe(
      'Complaint totals are not filtered by date because the imported file does not ' +
        'contain a Complaint Date column.',
    );
  });

  it('does not claim anything follows the range', () => {
    const text = noteText(scope({ undated_skus: 4, undated_complaints: 33 }));

    expect(text).not.toContain('follow the selected date range');
  });
});

describe('both kinds in one workspace', () => {
  const mixed = scope({
    filtered_by_date: true,
    dated_skus: 308,
    undated_skus: 883,
    undated_complaints: 5456,
  });

  it('names how many SKUs and complaints are not filtered', () => {
    expect(noteText(mixed)).toBe(
      'Some imported complaint records follow the selected date range. The remaining 883 SKUs ' +
        '(5,456 complaints) were imported without Complaint Dates, so their complaint ' +
        'totals are not date-filtered.',
    );
  });

  it('never says no Complaint Date column was provided', () => {
    // One was, for 308 SKUs. That sentence is the bug this file exists for.
    expect(noteText(mixed)).not.toContain('does not contain a Complaint Date column');
  });

  it('groups the counts the way every other figure on screen is grouped', () => {
    expect(noteText(mixed)).toContain('5,456 complaints');
  });
});

describe('counts and nouns agree', () => {
  /**
   * The live workspace sat at one undated SKU carrying one complaint, and the
   * note read "1 SKUs (1 complaints)".
   */
  it('says one SKU and one complaint in the singular', () => {
    const text = noteText(
      scope({
        filtered_by_date: true,
        dated_skus: 308,
        undated_skus: 1,
        undated_complaints: 1,
      }),
    );

    expect(text).toBe(
      'Some imported complaint records follow the selected date range. The remaining 1 SKU ' +
        '(1 complaint) were imported without Complaint Dates, so their complaint totals are ' +
        'not date-filtered.',
    );
  });

  it('keeps the plural wording for every other value', () => {
    const text = noteText(
      scope({ filtered_by_date: true, dated_skus: 1, undated_skus: 2, undated_complaints: 3 }),
    );

    expect(text).toContain('2 SKUs');
    expect(text).toContain('3 complaints');
  });

  it('pluralises independently — one SKU may carry several complaints', () => {
    const text = noteText(
      scope({ filtered_by_date: true, dated_skus: 1, undated_skus: 1, undated_complaints: 12 }),
    );

    expect(text).toContain('1 SKU ');
    expect(text).toContain('12 complaints');
  });

  it('...and several SKUs may carry one complaint between them', () => {
    const text = noteText(
      scope({ filtered_by_date: true, dated_skus: 1, undated_skus: 4, undated_complaints: 1 }),
    );

    expect(text).toContain('4 SKUs');
    expect(text).toContain('(1 complaint)');
  });

  it('still groups large counts', () => {
    const text = noteText(
      scope({
        filtered_by_date: true,
        dated_skus: 1,
        undated_skus: 883,
        undated_complaints: 5456,
      }),
    );

    expect(text).toContain('883 SKUs');
    expect(text).toContain('5,456 complaints');
  });
});

describe('nothing to say', () => {
  it('renders nothing when no SKU carries a complaint', () => {
    expect(noteText(scope({}))).toBeNull();
  });

  it('renders nothing before the payload has arrived', () => {
    expect(noteText(undefined)).toBeNull();
  });

  it('builds its wording from the counts alone', () => {
    /**
     * The payload carries no sentence any more — `note` was removed once it
     * became clear nothing rendered it and it had gone stale. Three numbers in,
     * one sentence out.
     */
    const text = noteText(
      scope({ filtered_by_date: true, dated_skus: 2, undated_skus: 7, undated_complaints: 9 }),
    );

    expect(text).toContain('7 SKUs');
    expect(text).toContain('9 complaints');
  });
});

/**
 * How far the dated record reaches.
 *
 * The case worth holding: a window that opens after the last dated complaint
 * returns zero from records that exist. On screen that is identical to a period
 * that genuinely had none, and it is the opposite thing to act on — one is good
 * news about the products, the other means the export is overdue.
 */
describe('how current the dated complaints are', () => {
  /** The note's text with a range start supplied. */
  function withSince(value: ComplaintScope, since?: string): string | null {
    render(<ComplaintScopeNote scope={value} since={since} />);
    return screen.queryByRole('note')?.textContent ?? null;
  }

  const dated = (over: Partial<ComplaintScope> = {}) =>
    scope({ filtered_by_date: true, dated_skus: 12, dated_through: '2026-07-30', ...over });

  it('names the last day the dated record covers', () => {
    const text = withSince(dated(), '2026-07-01');

    expect(text).toContain('Dated complaints run through 30 July 2026.');
  });

  it('warns when the selected range opens after that day', () => {
    const text = withSince(dated(), '2026-08-21');

    expect(text).toBe(
      'Complaint totals follow the selected date range. Dated complaints run through ' +
        '30 July 2026; a range after that date shows none.',
    );
  });

  it('does not warn when the range still reaches the record', () => {
    const text = withSince(dated(), '2026-07-30');

    expect(text).toContain('run through 30 July 2026.');
    expect(text).not.toContain('shows none');
  });

  it('does not warn when the range opens well before it', () => {
    expect(withSince(dated(), '2026-06-01')).not.toContain('shows none');
  });

  it('says nothing about dates when the field is null', () => {
    /** An aggregated sheet: no dated record exists, so there is no end to name. */
    const text = withSince(scope({ dated_skus: 0, undated_skus: 4, undated_complaints: 9 }), '2026-08-21');

    expect(text).not.toContain('Dated complaints run through');
  });

  it('says nothing about dates when the field is absent altogether', () => {
    /** An older server that does not send it yet. */
    const text = withSince(scope({ filtered_by_date: true, dated_skus: 12 }), '2026-08-21');

    expect(text).toBe('Complaint totals follow the selected date range.');
  });

  it('names the day but never warns when no range was supplied', () => {
    /** A caller with no period of its own cannot say the range ran past it. */
    const text = withSince(dated());

    expect(text).toContain('run through 30 July 2026.');
    expect(text).not.toContain('shows none');
  });

  it('carries the sentence into the mixed note too', () => {
    const text = withSince(
      dated({ undated_skus: 883, undated_complaints: 5456 }),
      '2026-08-21',
    );

    expect(text).toContain('were imported without Complaint Dates');
    expect(text).toContain('a range after that date shows none.');
  });

  it('ignores a date it cannot read rather than printing Invalid Date', () => {
    const text = withSince(dated({ dated_through: 'not-a-date' }), '2026-08-21');

    expect(text).toBe('Complaint totals follow the selected date range.');
  });
});

/**
 * Dates exist, and this page is not using them.
 *
 * The fourth state, and the one that is easy to get wrong: under
 * `?complaints=total` every SKU resolves as undated, so `dated_skus` arrives at
 * zero exactly as it does for a workspace that never had a Complaint Date
 * column. Only `dated_through` separates them. Without this branch, SKU
 * Performance would tell a workspace holding 253 dated SKUs that its file has
 * no such column — the wording that already sent one person hunting for an
 * importer bug that did not exist.
 */
describe('dates exist but this page is not filtering by them', () => {
  const wholeRecord = (over: Partial<ComplaintScope> = {}) =>
    scope({
      filtered_by_date: false,
      dated_skus: 0,
      undated_skus: 917,
      undated_complaints: 2284,
      dated_through: '2026-07-30',
      ...over,
    });

  it('says the figures are the whole record and names where the dates end', () => {
    const text = noteText(wholeRecord());

    expect(text).toBe(
      'Complaint totals are the full record for every SKU and do not follow the date range. ' +
        'Dated complaints run through 30 July 2026.',
    );
  });

  it('never claims the file has no Complaint Date column', () => {
    /** The whole reason this branch exists. */
    expect(noteText(wholeRecord())).not.toContain('does not contain a Complaint Date column');
  });

  it('still says the column is missing when it genuinely is', () => {
    /** An aggregated sheet: no dated row anywhere, so no date to report. */
    const text = noteText(scope({ dated_skus: 0, undated_skus: 4, undated_complaints: 9 }));

    expect(text).toBe(
      'Complaint totals are not filtered by date because the imported file does not contain ' +
        'a Complaint Date column.',
    );
  });

  it('falls back to the older sentence when the date cannot be read', () => {
    const text = noteText(wholeRecord({ dated_through: 'not-a-date' }));

    expect(text).toContain('does not contain a Complaint Date column');
    expect(text).not.toContain('Invalid Date');
  });

  it('does not warn about the range, because no range is being applied', () => {
    /** "A range after that date shows none" is a statement about filtering.
        Nothing is being filtered here, so it would be false. */
    render(<ComplaintScopeNote scope={wholeRecord()} since="2026-08-21" />);

    expect(screen.getByRole('note').textContent).not.toContain('shows none');
  });

  // The branch is narrower than the one it sits in front of, so it must not
  // capture a workspace that is dated, mixed, or empty. One render per test:
  // `cleanup` runs between tests, not between renders inside one.
  it('leaves the all-dated state as it was', () => {
    expect(noteText(scope({ filtered_by_date: true, dated_skus: 12 }))).toBe(
      'Complaint totals follow the selected date range.',
    );
  });

  it('leaves the mixed state as it was', () => {
    const text = noteText(
      scope({
        filtered_by_date: true,
        dated_skus: 308,
        undated_skus: 883,
        undated_complaints: 5456,
      }),
    );

    expect(text).toContain('Some imported complaint records follow the selected date range.');
  });

  it('still says nothing when there is nothing to say', () => {
    expect(noteText(scope({}))).toBeNull();
  });

  it('is not reached when some SKUs are still dated', () => {
    /** A mixed workspace under `range` has both a positive `dated_skus` and a
        `dated_through`; it must keep the mixed sentence. */
    const text = noteText(
      scope({
        filtered_by_date: true,
        dated_skus: 253,
        undated_skus: 670,
        undated_complaints: 1757,
        dated_through: '2026-07-30',
      }),
    );

    expect(text).toContain('Some imported complaint records');
    expect(text).not.toContain('are the full record for every SKU');
  });
});
