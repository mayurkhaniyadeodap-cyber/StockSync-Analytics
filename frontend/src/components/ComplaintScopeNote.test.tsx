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
