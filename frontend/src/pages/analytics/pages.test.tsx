// @vitest-environment jsdom
/**
 * The five Analytics pages.
 *
 * One file because they share a fixture and a render harness, and because the
 * property worth testing most is a relationship *between* them: no page shows a
 * figure it should not, and each stays focused on its own purpose.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ComplaintsPage } from './ComplaintsPage';
import { InventoryPage } from './InventoryPage';
import { OverviewPage } from './OverviewPage';
import { PerformancePage } from './PerformancePage';
import { SalesPage } from './SalesPage';
import {
  INSIGHTS,
  MIXED_SCOPE,
  PERFORMANCE,
  UNDATED_SCOPE,
  insightsWith,
  requested,
  routes,
} from '../../../tests/fixtures/analytics';
import { ToastProvider } from '../../contexts/ToastContext';

function renderPage(element: React.ReactNode) {
  return render(
    <MemoryRouter>
      <ToastProvider>{element}</ToastProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Analytics overview', () => {
  it('shows the six KPI cards and no more', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<OverviewPage />);

    await screen.findByText('Total SKUs');
    for (const label of [
      'Total SKUs',
      'Total Qty',
      'Shopify Sales',
      'Shopify Sales %',
      'Total Orders',
      'Total Complaints',
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    // The two derived averages belong to the pages that use them.
    expect(screen.queryByText('Avg Sales per SKU')).toBeNull();
    expect(screen.queryByText('Avg Complaint Rate')).toBeNull();
    // The first grid is the KPI row; the Quick insights panel below is also a
    // .cardgrid of .kpi, so this is scoped rather than counted page-wide.
    const kpiRow = container.querySelector('.cardgrid') as HTMLElement;
    expect(kpiRow.querySelectorAll('.kpi')).toHaveLength(6);
  });

  it('shows the four named findings', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<OverviewPage />);

    expect(await screen.findByText('Best selling SKU')).toBeDefined();
    expect(screen.getByText('Highest complaint SKU')).toBeDefined();
    expect(screen.getByText('Low stock alert')).toBeDefined();
    expect(screen.getByText('Zero sales SKU')).toBeDefined();
  });

  it('keeps a long SKU inside its insight card', async () => {
    // 48 characters, no hyphen or space to break on — the shape that used to
    // widen its column and drag the row's height with it.
    const long = 'SUPPLIERCODEWAREHOUSEBLACKEDITION0000000000000001';
    vi.stubGlobal(
      'fetch',
      insightsWith({
        sales: { ...INSIGHTS.sales, highest: { ...INSIGHTS.sales.highest!, sku: long } },
      }),
    );
    const { container } = renderPage(<OverviewPage />);

    await screen.findByText('Best selling SKU');
    const sku = container.querySelector('.insight-sku') as HTMLElement;
    // Clamped to two lines in the card, but carried whole in the tooltip: the
    // ellipsis is a reading aid and must not be the only copy of the name.
    expect(sku.textContent).toBe(long);
    expect(sku.getAttribute('title')).toBe(long);
  });

  it('gives the badge its own line, below the SKU and above the note', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<OverviewPage />);

    await screen.findByText('Best selling SKU');
    const card = container.querySelector('.kpi.insight') as HTMLElement;
    const badge = card.querySelector('.insight-metric .badge') as HTMLElement;
    const note = card.querySelector('.insight-note') as HTMLElement;

    // The two used to share one element, which left the figure starting at a
    // different point on every card.
    expect(badge.textContent).toBe('300 units');
    expect(note.contains(badge)).toBe(false);
    expect(note.textContent).toContain('of everything the store sold');
  });

  it('carries no large tables — that is what the sub-pages are for', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<OverviewPage />);

    await screen.findByText('Total SKUs');
    expect(container.querySelectorAll('table')).toHaveLength(0);
  });

  it('links onward to the pages that explain the charts', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<OverviewPage />);

    expect(await screen.findByRole('button', { name: /Sales analytics/ })).toBeDefined();
    expect(screen.getByRole('button', { name: /Complaint analytics/ })).toBeDefined();
  });

  it('says why complaints are a mix rather than a trend', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<OverviewPage />);

    expect(await screen.findByText(/without dates/)).toBeDefined();
  });

  it('reports an empty workspace once, not six times', async () => {
    vi.stubGlobal('fetch', insightsWith({ has_data: false }));
    renderPage(<OverviewPage />);

    expect(await screen.findByText('Nothing to analyse yet')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Import inventory' })).toBeDefined();
    expect(screen.queryByText('Quick insights')).toBeNull();
  });
});

describe('Sales Analytics', () => {
  it('shows its three KPI cards', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<SalesPage />);

    await screen.findByText('Avg Sales per SKU');
    expect(screen.getByText('Highest Selling SKU')).toBeDefined();
    expect(screen.getAllByText('Shopify Sales').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Shopify Sales %').length).toBeGreaterThan(0);
  });

  it('never mentions complaints', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<SalesPage />);

    await screen.findByText('Avg Sales per SKU');
    expect(container.textContent).not.toContain('Complaint');
  });

  it('draws the trend, the distribution and both rankings', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<SalesPage />);

    await screen.findByText('Sales trend');
    expect(screen.getByText('Sales distribution')).toBeDefined();
    expect(screen.getByText('Top selling SKUs')).toBeDefined();
    expect(screen.getByText('Lowest selling SKUs')).toBeDefined();
    expect(container.querySelectorAll('.chart-wrap svg').length).toBeGreaterThanOrEqual(4);
  });

  it('carries no SKU table of its own, and points at the one that has them', async () => {
    /**
     * Every per-SKU figure lives in one consolidated table now. A second,
     * narrower ranking here would be a subset of it with its own column set —
     * which is how two screens start disagreeing about the same SKU.
     */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<SalesPage />);

    await screen.findByText('Every SKU');
    expect(container.querySelectorAll('.tbl')).toHaveLength(0);
    expect(screen.getByRole('button', { name: /Open SKU performance/ })).toBeDefined();
  });

  it('says so when nothing sold rather than drawing an empty chart', async () => {
    vi.stubGlobal(
      'fetch',
      insightsWith({
        sales: {
          ...INSIGHTS.sales,
          shopify_sales: 0,
          highest: null,
          top: [],
          distribution: [],
        },
        trend: {
          ...INSIGHTS.trend,
          points: INSIGHTS.trend.points.map((p) => ({ ...p, units: 0 })),
        },
      }),
    );
    renderPage(<SalesPage />);

    expect(await screen.findByText(/no trend to plot yet/)).toBeDefined();
    expect(screen.getByText(/no distribution to show/)).toBeDefined();
  });
});

describe('Complaint Analytics', () => {
  it('shows its four KPI cards', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Most Complained SKU');
    // Scoped to the KPI row: "Total Complaints" is also
    // headers in the ranking table at the foot of this page.
    const kpiRow = container.querySelector('.cardgrid') as HTMLElement;
    const labels = [...kpiRow.querySelectorAll('.kpi-lbl')].map((el) => el.textContent);
    expect(labels).toEqual(['Total Complaints', 'Most Complained SKU', 'Affected SKUs']);
  });

  it('draws the distribution and the categories', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Complaint distribution');
    expect(screen.getByText('Complaint categories')).toBeDefined();
    expect(container.querySelectorAll('.chart-wrap svg').length).toBeGreaterThanOrEqual(2);
  });

  it('carries no trend section at all, and no note about one', async () => {
    /**
     * There is nothing to plot — the sheet holds one total per SKU per
     * category with no dates — and a panel explaining that spent a half-width
     * slot on prose about a chart that never existed. The absence is the
     * design; this test is what keeps a placeholder from growing back.
     */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Complaint distribution');
    expect(screen.queryByText('Complaint trend')).toBeNull();
    expect(container.textContent).not.toMatch(/snapshot, not a series/);
    expect(container.textContent).not.toMatch(/cannot be plotted over time/);
    expect(container.querySelector('.notice')).toBeNull();
  });

  it('gives the SKU chart the full width', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<ComplaintsPage />);

    const heading = await screen.findByText('Top complaint SKUs');
    const panel = heading.closest('.panel');
    // Not inside a two-column grid, which is what halved it before.
    expect(panel?.parentElement?.classList.contains('grid2')).toBe(false);
  });

  it('prints each category count with its share, not one or the other', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Complaint categories');
    const chart = [...container.querySelectorAll('.chart-wrap svg')].find((svg) =>
      svg.getAttribute('aria-label')?.includes('category'),
    );
    const text = [...(chart?.querySelectorAll('text') ?? [])].map((el) => el.textContent);
    const first = INSIGHTS.complaints.categories.filter((c) => c.count > 0)[0];
    expect(text).toContain(String(first?.count));
    expect(text).toContain(`${String(first?.share_pct.toFixed(2))}%`);
  });

  it('shows each SKU’s complaint count beside its rate', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Top complaint SKUs');
    const chart = [...container.querySelectorAll('.chart-wrap svg')].find((svg) =>
      svg.getAttribute('aria-label')?.includes('SKU'),
    );
    const text = [...(chart?.querySelectorAll('text') ?? [])].map((el) => el.textContent);
    const worst = INSIGHTS.complaints.top_skus[0];
    expect(text).toContain(String(worst?.total_complaints));
    expect(text).toContain(`${String(worst?.total_qty)} in stock`);
  });

  it('names every slice in the legend, not the first six', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Complaint distribution');
    const legend = container.querySelector('.legend');
    const withCounts = INSIGHTS.complaints.categories.filter((c) => c.count > 0);
    expect(legend?.querySelectorAll('span').length).toBeGreaterThanOrEqual(withCounts.length);
    for (const category of withCounts) {
      expect(legend?.textContent).toContain(category.label);
    }
  });

  it('carries no SKU table of its own, and points at the one that has them', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<ComplaintsPage />);

    await screen.findByText('Every SKU');
    expect(container.querySelectorAll('.tbl')).toHaveLength(0);
    expect(screen.getByRole('button', { name: /Open SKU performance/ })).toBeDefined();
  });

  it('says a clean store is clean', async () => {
    vi.stubGlobal(
      'fetch',
      insightsWith({
        complaints: {
          ...INSIGHTS.complaints,
          total_complaints: 0,
          most_complained: null,
          top_skus: [],
          categories: INSIGHTS.complaints.categories.map((c) => ({ ...c, count: 0 })),
        },
      }),
    );
    renderPage(<ComplaintsPage />);

    expect(await screen.findByText(/No complaints are recorded/)).toBeDefined();
  });

  it('confirms the range applies when every complaint is dated', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<ComplaintsPage />);

    await screen.findByText('Complaint Analytics');
    expect(screen.getByText('Complaint totals follow the selected date range.')).toBeDefined();
  });

  it('explains an aggregated import, whose totals ignore the range', async () => {
    /**
     * The page is entirely complaint figures, so this is where a reader is most
     * likely to assume the range applies to them. It does not when the file had
     * no Complaint Date column.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/insights': {
          ok: true,
          status: 200,
          body: { ...INSIGHTS, complaint_scope: UNDATED_SCOPE },
        },
      }),
    );
    renderPage(<ComplaintsPage />);

    const note = await screen.findByText(/not filtered by date/);
    expect(note.textContent).toContain('does not contain a Complaint Date column');
  });

  it('names the unfiltered remainder on a mixed workspace', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/insights': {
          ok: true,
          status: 200,
          body: { ...INSIGHTS, complaint_scope: MIXED_SCOPE },
        },
      }),
    );
    renderPage(<ComplaintsPage />);

    const note = await screen.findByText(/Some imported complaint records/);
    expect(note.textContent).toContain('883 SKUs');
    expect(note.textContent).toContain('5,456 complaints');
  });
});

describe('Inventory Insights', () => {
  it('shows all six findings', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<InventoryPage />);

    await screen.findByText('High stock, low sales');
    for (const heading of [
      'Low stock, high sales',
      'Zero sales SKUs',
      'Highest complaint SKUs',
      'Restock needed',
      'Overstocked item',
    ]) {
      expect(screen.getByText(heading)).toBeDefined();
    }
  });

  it('carries a recommendation badge per finding', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<InventoryPage />);

    expect(await screen.findByText('Slow down buying')).toBeDefined();
    expect(screen.getByText('Reorder')).toBeDefined();
    expect(screen.getByText('Review or discount')).toBeDefined();
    expect(screen.getByText('Investigate quality')).toBeDefined();
  });

  it('states the cut each list was made at', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<InventoryPage />);

    expect(await screen.findByText(/Over 70 units, under 13 sold/)).toBeDefined();
    expect(screen.getByText(/Under 70 units, over 13 sold/)).toBeDefined();
  });

  it('drops a recommendation when its list is empty', async () => {
    vi.stubGlobal(
      'fetch',
      insightsWith({
        inventory: { ...INSIGHTS.inventory, zero_sales: [], zero_sales_total: 0 },
      }),
    );
    renderPage(<InventoryPage />);

    await screen.findByText('Zero sales SKUs');
    expect(screen.queryByText('Review or discount')).toBeNull();
    expect(screen.getByText(/Every SKU holding stock has sold/)).toBeDefined();
  });
});

describe('SKU Performance', () => {
  it('shows every column the design asks for, in order', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const headers = [...container.querySelectorAll('.tbl th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).toEqual([
      'SKU',
      'Complaints',
      'Shopify Sales',
      'Shopify Sales %',
      'Total Quantity',
      'Total Orders',
      'Missing',
      'Missing Part',
      'Wrong Item Delivered',
      'Order Wrong Parcel',
      'Item Defect Partial',
      'Item Defect Complete',
      'Item Damage Partial',
      'Item Damage Complete',
      'Electronics Nonworking Partial',
      'Electronics Nonworking Complete',
    ]);
  });

  it('carries no Complaint Rate % column', async () => {
    /** The metric was removed from the project; the count stays. */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const headers = [...container.querySelectorAll('.tbl th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).toContain('Complaints');
    expect(headers.join(' ')).not.toContain('Complaint Rate');
  });

  it('offers no complaint-rate filter', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(screen.queryByLabelText(/complaint rate/i)).toBeNull();
  });

  it('never asks the server to sort or filter by it', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    for (const url of requested(fetch, '/analytics/performance')) {
      expect(url).not.toContain('complaint_rate');
      expect(url).not.toContain('min_complaint_pct');
    }
  });

  it('says which columns move with the range and which do not', async () => {
    /**
     * The range bounds the Shopify figures and the sheet's own columns do not
     * move at all. Complaints are named in neither list on purpose: whether
     * they answer the range depends on the file they came from, which
     * ComplaintScopeNote states when it is worth stating.
     */
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const note = screen.getByText(/do not change with the range/);
    expect(note.textContent).toContain('the last 30 days');
    expect(note.textContent).toContain('Total Quantity');
    expect(note.textContent).not.toContain('Complaints');
    expect(note.textContent).not.toContain('Complaint Rate');
  });

  it('renders Shopify Sales % from the payload, to two decimals', async () => {
    /**
     * Not computed here — the server divides, the page prints. This pins the
     * printing: two decimals, and the value the API sent.
     */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const cells = [
      ...(container.querySelectorAll('.tbl tbody tr')[1]?.querySelectorAll('td') ?? []),
    ].map((td) => td.textContent);

    // DD-1002 in the fixture: shopify_sales_pct 11.76.
    expect(cells[3]).toBe('11.76%');
  });

  it('carries no Rank and no Stock Status column', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const headers = [...container.querySelectorAll('.tbl th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).not.toContain('Rank');
    expect(headers).not.toContain('Status');
  });

  it('keeps the status filter even though the column is gone', async () => {
    /** Removing the badge does not remove the ability to narrow by it. */
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(screen.getByLabelText('Status')).toBeDefined();
  });

  it('shows each complaint category as its own column', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const first = [
      ...(container.querySelectorAll('.tbl tbody tr')[0]?.querySelectorAll('td') ?? []),
    ];
    // 6 summary columns + 10 categories.
    expect(first).toHaveLength(16);
  });

  it('prints the percentage to two decimals', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const cells = [
      ...(container.querySelectorAll('.tbl tbody tr')[0]?.querySelectorAll('td') ?? []),
    ].map((td) => td.textContent ?? '');
    // Shopify Sales %, the only percentage column left.
    expect(cells[3]).toMatch(/^\d+\.\d{2}%$/);
  });

  it('offers the four presets and a custom range', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const group = screen.getByRole('group', { name: 'Date range' });
    const labels = [...group.querySelectorAll('button')].map((b) => b.textContent?.trim());
    expect(labels).toEqual(['30D', '60D', '90D', '180D', 'Custom']);
  });

  it('opens on 30 days', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await waitFor(() => {
      expect(requested(fetch, '/analytics/performance').at(-1)).toContain('days=30');
    });
  });

  it('recalculates over the preset that was clicked', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.click(screen.getByRole('button', { name: '180D' }));

    await waitFor(() => {
      const last = requested(fetch, '/analytics/performance').at(-1) ?? '';
      expect(last).toContain('days=180');
      expect(last).not.toContain('days=30');
    });
  });

  it('asks for nothing until a custom range has both of its dates', async () => {
    /**
     * A half-given range describes a window the user has not finished naming.
     * Reloading on the first date would swap the rows out mid-edit.
     */
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.click(screen.getByRole('button', { name: /Custom/ }));
    const before = requested(fetch, '/analytics/performance').length;

    await userEvent.clear(screen.getByLabelText('End date'));
    expect(requested(fetch, '/analytics/performance')).toHaveLength(before);
    expect(screen.getByText('Pick both dates')).toBeDefined();
  });

  it('sends since and until once a custom range is complete', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.click(screen.getByRole('button', { name: /Custom/ }));
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2026-03-31' } });
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-03-01' } });

    await waitFor(() => {
      const last = requested(fetch, '/analytics/performance').at(-1) ?? '';
      expect(last).toContain('since=2026-03-01');
      expect(last).toContain('until=2026-03-31');
      // The preset and the pair would describe two different windows.
      expect(last).not.toContain('days=');
    });
  });

  it('refuses a backwards custom range rather than asking for it', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.click(screen.getByRole('button', { name: /Custom/ }));
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2026-03-01' } });
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-03-31' } });

    expect(screen.getByText('Start date must come first')).toBeDefined();
    await waitFor(() => {
      expect(requested(fetch, '/analytics/performance').at(-1)).not.toContain(
        'since=2026-03-31',
      );
    });
  });

  it('exports the same window the table is showing', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { assign });
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.click(screen.getByRole('button', { name: '90D' }));
    await userEvent.click(screen.getByRole('button', { name: /CSV/ }));

    expect(String(assign.mock.calls[0]?.[0])).toContain('days=90');
  });

  it('says which window the rows cover', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(screen.getByText(/Top 50 Most Complained SKUs over the last 30 days/)).toBeDefined();
  });

  it('says which figures move with the range and which do not', async () => {
    /**
     * Two of the columns are Shopify's and move with the range; the sheet's own
     * counts do not. Nothing on the row says so on its own.
     */
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const note = screen.getByText(/most recent import/);
    expect(note.textContent).toContain('the last 30 days');
    expect(note.textContent).toContain('Total Orders');
  });

  it('confirms the range applies when every complaint is dated', async () => {
    // The question a reader actually has is "does my date filter reach these
    // numbers?". Silence answers it only by implication.
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(screen.getByText('Complaint totals follow the selected date range.')).toBeDefined();
    expect(screen.queryByText(/not filtered/)).toBeNull();
  });

  it('explains an undated import instead of showing a range-looking figure', async () => {
    /**
     * The other upload format. An aggregated sheet has no Complaint Date column,
     * so "33 complaints" beside "the last 30 days" is the whole tally, not a
     * 30-day one — and without this note a reader has no way to tell.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: { ...PERFORMANCE, complaint_scope: UNDATED_SCOPE },
        },
      }),
    );
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const note = await screen.findByText(/not filtered by date/);
    expect(note.textContent).toBe(
      'Complaint totals are not filtered by date because the imported file does not ' +
        'contain a Complaint Date column.',
    );
  });

  it('names how much is unfiltered when the workspace holds both kinds', async () => {
    /**
     * The state a real store reaches after importing twice. Saying "no Complaint
     * Date column was provided" here reads as "your dates were ignored" — it
     * sent someone hunting for an importer bug that did not exist.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: { ...PERFORMANCE, complaint_scope: MIXED_SCOPE },
        },
      }),
    );
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    const note = await screen.findByText(/Some imported complaint records/);
    expect(note.textContent).toBe(
      'Some imported complaint records follow the selected date range. The remaining 883 SKUs ' +
        '(5,456 complaints) were imported without Complaint Dates, so their complaint ' +
        'totals are not date-filtered.',
    );
    // The all-undated sentence must not appear beside 308 dated SKUs.
    expect(screen.queryByText(/does not contain a Complaint Date column/)).toBeNull();
  });

  it('keeps the table sticky and horizontally scrollable', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(container.querySelector('.tbl-scroll')).not.toBeNull();
    expect(container.querySelector('table.tbl.sticky-1')).not.toBeNull();
  });

  it('asks for the top 50 and never for a page', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await waitFor(() => {
      const asked = requested(fetch, '/analytics/performance');
      expect(asked.length).toBeGreaterThan(0);
      for (const url of asked) {
        expect(url).toContain('limit=50');
        expect(url).not.toContain('offset=');
      }
    });
  });

  it('opens ranked by total complaints, worst first', async () => {
    /** The same order the Dashboard opens in — one table, one ranking. */
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await waitFor(() => {
      const first = requested(fetch, '/analytics/performance')[0] ?? '';
      expect(first).toContain('sort=total_complaints');
      expect(first).toContain('descending=true');
    });
  });

  it('has no pager', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(screen.queryByRole('button', { name: 'Next' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Previous' })).toBeNull();
  });

  it('sorts on a clicked column, descending first', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.click(screen.getByRole('button', { name: /Total Orders/ }));

    await waitFor(() => {
      const last = requested(fetch, '/analytics/performance').at(-1) ?? '';
      expect(last).toContain('sort=total_orders');
      expect(last).toContain('descending=true');
    });
  });

  it('sends only the filters that are set', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<PerformancePage />);

    await screen.findByText('Filters');
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'critical');

    await waitFor(() => {
      const last = requested(fetch, '/analytics/performance').at(-1) ?? '';
      expect(last).toContain('status=critical');
      expect(last).not.toContain('min_qty=');
    });
  });

  it('builds the category list from the payload', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    const select = await screen.findByLabelText('Complaint category');
    const options = [...(select as HTMLSelectElement).options].map((o) => o.value);
    expect(options).toEqual(['', 'item_defect_partial', 'item_damage_partial']);
  });

  it('exports the filtered set, not the page', async () => {
    const assign = vi.fn();
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('location', { ...window.location, assign });
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'critical');
    // Wait for the debounce to reach the server: the export URL is built from the
    // *applied* filters, so clicking sooner would test the wrong query.
    await waitFor(() => {
      expect(requested(fetch, '/analytics/performance').at(-1)).toContain('status=critical');
    });

    await userEvent.click(screen.getByRole('button', { name: /CSV/ }));

    const url = String(assign.mock.calls.at(-1)?.[0] ?? '');
    expect(url).toContain('/analytics/performance/export');
    expect(url).toContain('format=csv');
    expect(url).toContain('status=critical');
    // Paging is deliberately absent: an export of one page is a trap.
    expect(url).not.toContain('offset=');
    expect(url).not.toContain('limit=');
  });

  it('offers both formats', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<PerformancePage />);

    await screen.findByText('SKU Performance');
    expect(screen.getByRole('button', { name: /CSV/ })).toBeDefined();
    expect(screen.getByRole('button', { name: /Excel/ })).toBeDefined();
  });

  it('cannot export nothing', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: { ...PERFORMANCE, rows: [], total: 0 },
        },
      }),
    );
    renderPage(<PerformancePage />);

    await screen.findByText('Nothing matches');
    expect(screen.getByRole('button', { name: /CSV/ }).hasAttribute('disabled')).toBe(true);
  });

  it('says what it is showing and what it is leaving out', async () => {
    /**
     * The pager is gone, but the total is not. A table that quietly stops at
     * fifty reads as a workspace with fifty SKUs.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: { ...PERFORMANCE, total: 120 },
        },
      }),
    );
    renderPage(<PerformancePage />);

    expect(await screen.findByText(/Top 3 most complained of 120 SKUs/)).toBeDefined();
  });

  it('counts matches rather than the top 50 while filtering', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: { ...PERFORMANCE, total: 120 },
        },
      }),
    );
    renderPage(<PerformancePage />);
    await screen.findByText('SKU Performance');

    await userEvent.type(screen.getByLabelText('Search SKU'), 'DD');

    expect(await screen.findByText(/Showing 3 of 120 matching SKUs/)).toBeDefined();
  });
});

describe('across the pages', () => {
  it('each fetches the range it was given', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage(<SalesPage />);

    await screen.findByText('Avg Sales per SKU');
    await userEvent.click(screen.getByRole('button', { name: '7D' }));

    await waitFor(() => {
      expect(requested(fetch, '/analytics/insights').at(-1)).toContain('days=7');
    });
  });

  it.each([
    ['Sales Analytics', <SalesPage key="s" />],
    ['Complaint Analytics', <ComplaintsPage key="c" />],
    ['Inventory Insights', <InventoryPage key="i" />],
  ])('%s reports a failed load with a retry', async (_name, element) => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/insights': {
          ok: false,
          status: 500,
          body: {
            error: {
              code: 'internal_error',
              message: 'Something went wrong.',
              next: 'Try again.',
            },
          },
        },
      }),
    );
    renderPage(element);

    expect(await screen.findByText(/Something went wrong/)).toBeDefined();
    expect(screen.getByRole('button', { name: /Retry/ })).toBeDefined();
  });

  it('warns once when the rollup is behind the last sync', async () => {
    vi.stubGlobal('fetch', insightsWith({ stale: true }));
    renderPage(<ComplaintsPage />);

    expect(await screen.findByText(/behind the last sync/)).toBeDefined();
  });
});

describe('the sales trend states what it counts', () => {
  /**
   * The trend sums every SKU the store sold; the "Shopify Sales" card counts
   * only SKUs found in the imported sheet. On the live workspace that is
   * 1,352,080 against 570,435 — the chart totals more than twice the card above
   * it. Both are right, and nothing on screen used to say why.
   */
  it('explains the scope on Sales Analytics', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<SalesPage />);

    await screen.findByText('Sales trend');
    expect(screen.getByText(/all Shopify sales/)).toBeDefined();
    expect(screen.getByText(/only SKUs imported into StockSync Analytics/)).toBeDefined();
  });

  it('explains the scope on the Analytics overview', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage(<OverviewPage />);

    await screen.findByText('Sales trend');
    expect(screen.getByText(/all Shopify sales/)).toBeDefined();
  });

  it('sits with the chart, not inside it', async () => {
    /** .chart-wrap has its own padding and is the tooltip positioning context. */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage(<OverviewPage />);

    await screen.findByText('Sales trend');
    const note = container.querySelector('.trend-scope');
    expect(note).not.toBeNull();
    expect(note?.closest('.chart-wrap')).toBeNull();
  });
});
