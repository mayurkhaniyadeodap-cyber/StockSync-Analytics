// @vitest-environment jsdom
/**
 * The navigation rail.
 *
 * Worth a test because the sidebar is the one place that decides what the app
 * appears to contain: a route that exists but is not listed is a feature nobody
 * finds, and a listed route that does not exist is a dead end.
 */

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Sidebar } from './Sidebar';

function renderSidebar(path = '/dashboard') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar
        collapsed={false}
        open={false}
        onToggleCollapsed={vi.fn()}
        onNavigate={vi.fn()}
      />
    </MemoryRouter>,
  );
}

/** Every link in the rail, in the order it is rendered. */
function links(container: HTMLElement): { label: string; href: string | null }[] {
  return [...container.querySelectorAll('.side-nav a')].map((a) => ({
    label: a.textContent ?? '',
    href: a.getAttribute('href'),
  }));
}

afterEach(cleanup);

describe('Sidebar', () => {
  it('lists all twelve destinations, in the order the work happens', () => {
    /**
     * Every Analytics page is listed outright. They used to be children that
     * appeared only while the section was open, which hid four of the twelve
     * pages behind a click and made the rail a poor answer to "what is in this
     * product".
     */
    const { container } = renderSidebar();

    expect(links(container)).toEqual([
      { label: 'Dashboard', href: '/dashboard' },
      { label: 'Import Data', href: '/import' },
      { label: 'Import History', href: '/import-history' },
      { label: 'Shopify Connection', href: '/shopify' },
      { label: 'Sync History', href: '/sync-history' },
      { label: 'Overview', href: '/analytics' },
      { label: 'Sales Analytics', href: '/analytics/sales' },
      { label: 'Complaint Analytics', href: '/analytics/complaints' },
      { label: 'Inventory Analytics', href: '/analytics/inventory' },
      { label: 'Product Performance', href: '/analytics/performance' },
      { label: 'Reports', href: '/reports' },
      { label: 'Settings', href: '/settings' },
    ]);
  });

  it('groups the sets and leaves the daily path unlabelled', () => {
    /** A heading earns its line by introducing a set. The five operational
        destinations at the top are the path through the product and need no
        heading to explain them. */
    const { container } = renderSidebar();

    const headings = [...container.querySelectorAll('.side-grp')].map((el) => el.textContent);
    expect(headings).toEqual(['Analytics', 'Reports', 'Settings']);
  });

  it('shows every Analytics page without opening the section first', () => {
    renderSidebar('/dashboard');

    for (const label of [
      'Sales Analytics',
      'Complaint Analytics',
      'Inventory Analytics',
      'Product Performance',
    ]) {
      expect(screen.getByRole('link', { name: label })).toBeDefined();
    }
  });

  it.each([
    ['/dashboard', 'Dashboard'],
    ['/analytics', 'Overview'],
    ['/analytics/sales', 'Sales Analytics'],
    ['/analytics/performance', 'Product Performance'],
    ['/reports', 'Reports'],
  ])('marks %s current, and nothing else', (path, label) => {
    const { container } = renderSidebar(path);

    const current = [...container.querySelectorAll('.side-nav a[aria-current="page"]')].map(
      (a) => a.textContent,
    );
    expect(current).toEqual([label]);
  });

  it('does not light up Overview while a sibling Analytics page is open', () => {
    /**
     * They are two entries in one list now, not a parent and a child, and two
     * highlighted rows read as two current pages. `end` on the link is what
     * stops /analytics matching /analytics/sales.
     */
    renderSidebar('/analytics/sales');

    expect(
      screen.getByRole('link', { name: 'Overview' }).getAttribute('aria-current'),
    ).toBeNull();
    expect(
      screen.getByRole('link', { name: 'Sales Analytics' }).getAttribute('aria-current'),
    ).toBe('page');
  });

  it('keeps Settings current on its own sub-routes', () => {
    /** `/settings/profile` is a tab within the one page, not a page of its own,
        so the rail must not go blank when you open one. */
    renderSidebar('/settings/profile');

    const link = screen.getByRole('link', { name: 'Settings' });
    expect(link.className).toContain('on');
  });

  it('gives every link an accessible name that survives collapsing', () => {
    /** The label is hidden at 72px, so the name has to come from somewhere
        else or the rail becomes unusable to a screen reader. */
    const { container } = renderSidebar();

    for (const a of container.querySelectorAll('.side-nav a')) {
      expect(a.getAttribute('aria-label')).toBeTruthy();
      expect(a.getAttribute('title')).toBeTruthy();
    }
  });
});
