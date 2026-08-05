// @vitest-environment jsdom
/**
 * The navigation groups.
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

afterEach(cleanup);

describe('Sidebar', () => {
  it('groups Analytics and Reports under Insights', () => {
    const { container } = renderSidebar();

    const group = [...container.querySelectorAll('.side-grp')].find(
      (element) => element.textContent === 'Insights',
    );
    expect(group).toBeDefined();

    // The two items in that group, in order, are the ones the group promises.
    const items = [...(group?.parentElement?.querySelectorAll('a') ?? [])].map(
      (a) => a.textContent,
    );
    expect(items).toEqual(['Analytics', 'Reports']);
  });

  it('hides the Analytics sub-pages until the section is open', () => {
    renderSidebar('/dashboard');

    expect(screen.queryByRole('link', { name: 'Sales Analytics' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Analytics' })).toBeDefined();
  });

  it.each([
    ['/analytics', 'the section landing page'],
    ['/analytics/sales', 'a sub-page'],
  ])('reveals all four sub-pages from %s (%s)', (path) => {
    renderSidebar(path);

    for (const label of [
      'Sales Analytics',
      'Complaint Analytics',
      'Inventory Insights',
      'SKU Performance',
    ]) {
      expect(screen.getByRole('link', { name: label })).toBeDefined();
    }
  });

  it.each([
    ['Sales Analytics', '/analytics/sales'],
    ['Complaint Analytics', '/analytics/complaints'],
    ['Inventory Insights', '/analytics/inventory'],
    ['SKU Performance', '/analytics/performance'],
  ])('links %s at %s', (label, href) => {
    renderSidebar('/analytics');

    expect(screen.getByRole('link', { name: label }).getAttribute('href')).toBe(href);
  });

  it('marks the open sub-page current without also marking its parent', () => {
    // `end` on the parent stops /analytics matching /analytics/sales, so the
    // breadcrumb reads as one current page rather than two.
    renderSidebar('/analytics/sales');

    expect(
      screen.getByRole('link', { name: 'Sales Analytics' }).getAttribute('aria-current'),
    ).toBe('page');
    expect(
      screen.getByRole('link', { name: 'Analytics' }).getAttribute('aria-current'),
    ).toBeNull();
  });

  it('indents the sub-pages', () => {
    const { container } = renderSidebar('/analytics');

    expect(container.querySelectorAll('.nav.sub')).toHaveLength(4);
  });

  it('links Analytics at /analytics', () => {
    renderSidebar();

    expect(screen.getByRole('link', { name: 'Analytics' }).getAttribute('href')).toBe(
      '/analytics',
    );
  });

  it('marks Analytics current when it is the open route', () => {
    renderSidebar('/analytics');

    // react-router sets aria-current on the active NavLink.
    expect(screen.getByRole('link', { name: 'Analytics' }).getAttribute('aria-current')).toBe(
      'page',
    );
    expect(
      screen.getByRole('link', { name: 'Dashboard' }).getAttribute('aria-current'),
    ).toBeNull();
  });

  it('keeps the Dashboard its own ungrouped entry', () => {
    renderSidebar();

    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'Reports' })).toBeDefined();
  });
});
