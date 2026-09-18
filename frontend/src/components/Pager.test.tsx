// @vitest-environment jsdom
/**
 * The pager's windowing.
 *
 * Worth its own test because the arithmetic is the kind that looks right and is
 * off by one: which page is current, which window of numbers is shown, and what
 * row range the sentence claims.
 */

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Pager } from './Pager';

afterEach(cleanup);

/** The numbered buttons, in order, ignoring the two arrows. */
function numbers(): string[] {
  return screen
    .getAllByRole('button')
    .map((b) => b.textContent ?? '')
    .filter((text) => text !== '');
}

describe('Pager', () => {
  it('renders nothing when everything fits on one page', () => {
    /** A pager that only ever shows a disabled "1" is furniture. */
    const { container } = render(
      <Pager offset={0} limit={50} total={50} onGo={vi.fn()} unit="imports" />,
    );

    expect(container.firstChild).toBeNull();
  });

  it('states the row range, not just the page number', () => {
    /** On a reverse-chronological list "page 3" alone says nothing about what
        you are looking at. */
    render(<Pager offset={100} limit={50} total={214} onGo={vi.fn()} unit="imports" />);

    expect(screen.getByText('Showing 101–150 of 214 imports')).toBeDefined();
  });

  it('does not claim rows past the end on the last page', () => {
    render(<Pager offset={200} limit={50} total={214} onGo={vi.fn()} unit="imports" />);

    expect(screen.getByText('Showing 201–214 of 214 imports')).toBeDefined();
  });

  it('marks exactly one page current', () => {
    render(<Pager offset={100} limit={50} total={500} onGo={vi.fn()} />);

    const current = screen.getAllByRole('button').filter((b) => b.ariaCurrent === 'page');
    expect(current).toHaveLength(1);
    expect(current[0]?.textContent).toBe('3');
  });

  it('keeps the window anchored at the start rather than running negative', () => {
    render(<Pager offset={0} limit={50} total={500} onGo={vi.fn()} />);

    expect(numbers()).toEqual(['1', '2', '3', '4', '5']);
  });

  it('slides the window so the current page stays near the middle', () => {
    render(<Pager offset={350} limit={50} total={500} onGo={vi.fn()} />);

    // Page 8 of 10.
    expect(numbers()).toEqual(['6', '7', '8', '9', '10']);
  });

  it('clamps the window at the end rather than running past it', () => {
    render(<Pager offset={450} limit={50} total={500} onGo={vi.fn()} />);

    expect(numbers()).toEqual(['6', '7', '8', '9', '10']);
  });

  it('shows every page when there are fewer than the window holds', () => {
    render(<Pager offset={0} limit={50} total={120} onGo={vi.fn()} />);

    expect(numbers()).toEqual(['1', '2', '3']);
  });

  it('hands back an offset, not a page number', () => {
    /** The caller pages by offset because that is what the endpoints take. */
    const onGo = vi.fn();
    render(<Pager offset={0} limit={50} total={500} onGo={onGo} />);

    void userEvent.setup().click(screen.getByRole('button', { name: 'Page 4' }));

    return vi.waitFor(() => {
      expect(onGo).toHaveBeenCalledWith(150);
    });
  });

  it('disables the arrow that would leave the set', () => {
    // jest-dom is not installed here, so `disabled` is read off the element
    // rather than through a matcher.
    const disabled = (name: string) =>
      (screen.getByRole('button', { name }) as HTMLButtonElement).disabled;

    const { rerender } = render(<Pager offset={0} limit={50} total={500} onGo={vi.fn()} />);
    expect(disabled('Previous page')).toBe(true);
    expect(disabled('Next page')).toBe(false);

    rerender(<Pager offset={450} limit={50} total={500} onGo={vi.fn()} />);
    expect(disabled('Previous page')).toBe(false);
    expect(disabled('Next page')).toBe(true);
  });
});
