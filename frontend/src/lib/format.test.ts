import { describe, expect, it } from 'vitest';

import { freshness, inr, n, pct, sharePct } from './format';

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
