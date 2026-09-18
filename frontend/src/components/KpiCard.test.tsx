// @vitest-environment jsdom
/**
 * The KPI card's one rule: a trend is a real comparison, a note is prose, and
 * the card never dresses one as the other.
 */

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { KpiCard } from './KpiCard';

afterEach(cleanup);

describe('KpiCard', () => {
  it('shows a note and no arrow when there is no prior period', () => {
    /**
     * Five of the six dashboard cards are sheet totals from the newest import,
     * and an import replaces the whole dataset — there is no previous value to
     * compare them against. The card must not imply one.
     */
    const { container } = render(
      <KpiCard label="Total SKUs" value="1,426" note="in the imported sheet" />,
    );

    expect(screen.getByText('in the imported sheet')).toBeDefined();
    expect(container.querySelector('.kpi-trend')).toBeNull();
  });

  it('colours a rise green when rising is the good direction', () => {
    const { container } = render(
      <KpiCard
        label="Shopify Sales"
        value="335,492"
        trend={{ value: '24.00%', up: true, good: true, against: 'vs. previous 30 days' }}
      />,
    );

    expect(container.querySelector('.kpi-trend')?.className).toContain('good');
    expect(screen.getByText('vs. previous 30 days')).toBeDefined();
  });

  it('colours a fall green when falling is the good direction', () => {
    /**
     * The sign does not decide the colour. Complaints going down is good news,
     * and painting it red because the delta is negative tells the reader the
     * opposite of what happened.
     */
    const { container } = render(
      <KpiCard
        label="Total Complaints"
        value="1,253"
        trend={{ value: '6.00%', up: false, good: true, against: 'vs. previous 30 days' }}
      />,
    );

    expect(container.querySelector('.kpi-trend')?.className).toContain('good');
    expect(container.querySelector('.kpi-trend')?.className).not.toContain('poor');
  });

  it('can carry both a trend and a note', () => {
    /** Shopify Sales needs both: how much it moved, and how much of the whole
        store this is. They answer different questions. */
    render(
      <KpiCard
        label="Shopify Sales"
        value="335,492"
        trend={{ value: '24.00%', up: true, good: true, against: 'vs. previous 30 days' }}
        note="4.70% of 7,21,407 units sold"
      />,
    );

    expect(screen.getByText('vs. previous 30 days')).toBeDefined();
    expect(screen.getByText('4.70% of 7,21,407 units sold')).toBeDefined();
  });

  it('hides the glyph from assistive technology', () => {
    /** It repeats the label beside it; announcing it is noise. */
    const { container } = render(<KpiCard label="Total Orders" value="4,892" icon="file" />);

    expect(container.querySelector('.kpi-ico')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders without a glyph at all, for the filter cards that have none', () => {
    const { container } = render(<KpiCard label="Total Orders" value="4,892" />);

    expect(container.querySelector('.kpi-ico')).toBeNull();
    expect(screen.getByText('4,892')).toBeDefined();
  });
});
