import { describe, expect, it } from 'vitest';

import { complaintRate, day, freshness, inr, n, pct, sharePct } from './format';

describe('n — Indian digit grouping', () => {
  it('groups in the lakh/crore pattern, not thousands', () => {
    expect(n(1240)).toBe('1,240');
    expect(n(52300)).toBe('52,300');
    // The distinguishing case: 1,490,000 in en-US, 14,90,000 in en-IN.
    expect(n(1490000)).toBe('14,90,000');
  });

  it('leaves small numbers alone', () => {
    expect(n(0)).toBe('0');
    expect(n(42)).toBe('42');
  });
});

describe('inr', () => {
  it('shows plain rupees below a lakh', () => {
    expect(inr(946)).toBe('₹946');
    expect(inr(99999)).toBe('₹99,999');
  });

  it('abbreviates to lakhs at 1,00,000', () => {
    expect(inr(100000)).toBe('₹1.00 L');
    expect(inr(842000)).toBe('₹8.42 L');
  });

  it('abbreviates to crores at 1,00,00,000', () => {
    expect(inr(10000000)).toBe('₹1.00 Cr');
    expect(inr(84200000)).toBe('₹8.42 Cr');
  });

  it('rounds rather than showing paise', () => {
    expect(inr(946.4)).toBe('₹946');
    expect(inr(946.6)).toBe('₹947');
  });
});

describe('pct', () => {
  it('always shows exactly two decimals', () => {
    expect(pct(28.6)).toBe('28.60%');
    expect(pct(100)).toBe('100.00%');
    expect(pct(0)).toBe('0.00%');
    expect(pct(0.642)).toBe('0.64%');
    expect(pct(42.195)).toBe('42.20%');
  });

  it('rounds rather than truncating', () => {
    expect(pct(0.099)).toBe('0.10%');
    expect(pct(9.999)).toBe('10.00%');
  });
});

describe('sharePct', () => {
  it('divides and multiplies out', () => {
    expect(sharePct(100, 1000)).toBe(10);
    expect(sharePct(1, 3)).toBeCloseTo(33.333, 3);
  });

  it('returns zero rather than dividing by zero', () => {
    /** An empty chart must render 0.00%, never NaN%. */
    expect(sharePct(5, 0)).toBe(0);
    expect(pct(sharePct(5, 0))).toBe('0.00%');
  });
});

describe('complaintRate', () => {
  it('is an ordinary percentage below the cap', () => {
    expect(complaintRate(218, 52_300)).toBeCloseTo(0.4168, 4);
    expect(complaintRate(1, 4)).toBe(25);
  });

  it('reports exactly 100 when every order drew a complaint', () => {
    expect(complaintRate(40, 40)).toBe(100);
  });

  it('caps above the whole rather than reporting 312.50%', () => {
    /**
     * Not a correction of the data. One order can draw several complaints, and
     * the date range moves the numerator while the denominator stays a
     * snapshot — so the ratio is real. It is the *reported* figure that stops
     * at 100, because a percentage past it reads as a bug rather than a number.
     */
    expect(complaintRate(25, 8)).toBe(100);
    expect(complaintRate(1_000_000, 1)).toBe(100);
    // The boundary: one complaint past the whole is already capped.
    expect(complaintRate(41, 40)).toBe(100);
  });

  it('is null with nothing to divide by, never zero and never NaN', () => {
    /** No rate is not a rate of zero, and the two are shown differently. */
    expect(complaintRate(5, 0)).toBeNull();
    expect(complaintRate(0, 0)).toBeNull();
    // Nonsense that arithmetic would otherwise turn into a negative percentage.
    expect(complaintRate(5, -1)).toBeNull();
  });

  it('formats to a rate a reader can act on', () => {
    expect(pct(complaintRate(25, 8) as number)).toBe('100.00%');
    expect(pct(complaintRate(218, 52_300) as number)).toBe('0.42%');
  });
});

describe('day', () => {
  it('reads a yyyy-mm-dd as a day a person would say', () => {
    expect(day('2026-07-30')).toBe('30 July 2026');
    expect(day('2026-01-01')).toBe('1 January 2026');
  });

  it('does not slip a day for readers west of Greenwich', () => {
    /**
     * `new Date('2026-07-30')` is parsed as UTC midnight and renders as the
     * 29th anywhere behind it. The explicit T00:00:00 makes it local midnight,
     * which is the same trap the trend labels already avoid.
     */
    expect(day('2026-07-30')).toContain('30 July');
  });

  it('returns null for nothing rather than a date for now', () => {
    expect(day(null)).toBeNull();
    expect(day(undefined)).toBeNull();
    expect(day('')).toBeNull();
  });

  it('returns null rather than "Invalid Date" for junk', () => {
    /** A caller can leave the sentence out; it cannot un-print nonsense. */
    expect(day('not-a-date')).toBeNull();
    expect(day('2026-13-45')).toBeNull();
  });
});

describe('freshness', () => {
  const now = new Date('2026-07-28T09:52:00Z');
  const ago = (ms: number) => new Date(now.getTime() - ms);

  it('reads "just now" under a minute', () => {
    expect(freshness(ago(30_000), now)).toBe('just now');
  });

  it('counts minutes, then hours, then days', () => {
    expect(freshness(ago(12 * 60_000), now)).toBe('12 minutes ago');
    expect(freshness(ago(3 * 3_600_000), now)).toBe('3 hours ago');
    expect(freshness(ago(2 * 86_400_000), now)).toBe('2 days ago');
  });

  it('singularises', () => {
    expect(freshness(ago(60_000), now)).toBe('1 minute ago');
    expect(freshness(ago(3_600_000), now)).toBe('1 hour ago');
    expect(freshness(ago(86_400_000), now)).toBe('1 day ago');
  });

  it('does not show a negative age when clocks disagree', () => {
    expect(freshness(new Date(now.getTime() + 5_000), now)).toBe('just now');
  });
});
