/**
 * The gap between the live store and what has been pulled.
 *
 * Worth its own tests because the three answers are easy to collapse into two,
 * and the one that must not be collapsed is "unknown": reporting "up to date"
 * when the truth is "never measured" is how a stale figure gets presented as a
 * fresh one.
 */

import { describe, expect, it } from 'vitest';

import { storeGap } from './storeGap';

const NOW = new Date('2026-09-18T12:00:00Z');

describe('storeGap', () => {
  it('says unknown when the check has never run', () => {
    const gap = storeGap(null, '2026-09-18T11:00:00Z', null, NOW);

    expect(gap.state).toBe('unknown');
    expect(gap.checkedAt).toBeNull();
  });

  it('says unknown when Shopify could not be reached, not "up to date"', () => {
    /** A failed check leaves the store's own timestamp unset. "We do not know"
        and "we are current" are different answers. */
    const gap = storeGap(null, '2026-09-18T11:00:00Z', '2026-09-18T11:05:00Z', NOW);

    expect(gap.state).toBe('unknown');
  });

  it('treats a small gap as current, because that is the steady state', () => {
    /** A sync takes minutes and orders arrive continuously, so a few minutes
        behind is normal rather than a fault. The tolerance matches the
        server's own. */
    const gap = storeGap(
      '2026-09-18T11:10:00Z',
      '2026-09-18T11:00:00Z',
      '2026-09-18T11:11:00Z',
      NOW,
    );

    expect(gap.state).toBe('current');
    expect(gap.label).toBe('Up to date with Shopify');
  });

  it('reports a real gap as behind, and says how far', () => {
    const gap = storeGap(
      '2026-09-18T11:00:00Z',
      '2026-09-18T05:00:00Z',
      '2026-09-18T11:05:00Z',
      NOW,
    );

    expect(gap.state).toBe('behind');
    expect(gap.label).toContain('6 hours');
  });

  it('reports a store with orders and nothing pulled as behind', () => {
    const gap = storeGap('2026-09-18T11:00:00Z', null, '2026-09-18T11:05:00Z', NOW);

    expect(gap.state).toBe('behind');
    expect(gap.label).toContain('none pulled yet');
  });

  it('carries when the gap was measured, so the card can age it', () => {
    /** A gap read two days ago is not a statement about now. */
    const gap = storeGap(
      '2026-09-18T11:00:00Z',
      '2026-09-18T11:00:00Z',
      '2026-09-16T09:00:00Z',
      NOW,
    );

    expect(gap.checkedAt?.toISOString()).toBe('2026-09-16T09:00:00.000Z');
  });
});
