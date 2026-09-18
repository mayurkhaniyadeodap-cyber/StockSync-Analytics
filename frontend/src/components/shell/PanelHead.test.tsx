// @vitest-environment jsdom
/**
 * The panel header, and the caveat it can fold away.
 *
 * The disclosure is worth a test because it hides something that used to be
 * always visible: each caveat stops a real misreading of the figure above it,
 * so "folded away" must mean one click, not gone.
 */

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { PanelHead } from './PanelHead';

afterEach(cleanup);

describe('PanelHead', () => {
  it('renders the title alone when that is all it is given', () => {
    const { container } = render(<PanelHead title="Export centre" />);

    expect(screen.getByRole('heading', { name: 'Export centre' })).toBeDefined();
    expect(container.querySelector('.p-chip')).toBeNull();
    expect(container.querySelector('.p-info')).toBeNull();
  });

  it('hides the glyph from assistive technology', () => {
    /** It repeats the title beside it; announcing it is noise. */
    const { container } = render(<PanelHead title="Inventory Health" icon="box" tone="moss" />);

    expect(container.querySelector('.p-chip')?.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('.p-chip')?.className).toContain('moss');
  });

  it('offers no disclosure when there is no caveat', () => {
    const { container } = render(<PanelHead title="Sync Status" hint="Last pull" />);

    expect(container.querySelector('.p-info')).toBeNull();
    expect(container.querySelector('.p-note')).toBeNull();
  });

  it('keeps the caveat one click away rather than removing it', async () => {
    render(<PanelHead title="Inventory Health" info="Low stock is at or below 10 units." />);

    const toggle = screen.getByRole('button', { name: 'What does Inventory Health cover?' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    // Rendered but hidden, so the control has something to point at and the
    // text is reachable to anything that reads the document rather than paints
    // it.
    expect(screen.getByText('Low stock is at or below 10 units.').hidden).toBe(true);

    await userEvent.setup().click(toggle);

    expect(screen.getByText('Low stock is at or below 10 units.').hidden).toBe(false);
    expect(
      screen
        .getByRole('button', { name: 'Hide what Inventory Health covers' })
        .getAttribute('aria-expanded'),
    ).toBe('true');
  });

  it('points the toggle at the caveat it opens', () => {
    /** Two panels on one row each have one; without a unique pairing the second
        toggle would open the first one's text. */
    const { container } = render(
      <>
        <PanelHead title="Inventory Health" info="First caveat." />
        <PanelHead title="Complaint Breakdown" info="Second caveat." />
      </>,
    );

    const toggles = [...container.querySelectorAll('.p-info')];
    const notes = [...container.querySelectorAll('.p-note')];
    expect(toggles).toHaveLength(2);

    for (const [i, toggle] of toggles.entries()) {
      expect(toggle.getAttribute('aria-controls')).toBe(notes[i]?.id);
    }
    expect(notes[0]?.id).not.toBe(notes[1]?.id);
  });
});
