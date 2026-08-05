// @vitest-environment jsdom
/** Dashboard: six cards from the sheet and Shopify, one SKU table. */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DashboardPage } from './DashboardPage';
import { ShopifyStatusProvider } from '../contexts/ShopifyStatusContext';
import { ToastProvider } from '../contexts/ToastContext';
import type {
  AnalyticsOverview,
  ConnectionState,
  Kpis,
  PerformanceRow,
  SalesSummary,
  SyncRun,
  Trend,
} from '../types/api';

/** The ten categories the server sends with every page, in sheet order. */
const COMPLAINT_COLUMNS = [
  { field: 'item_defect_partial', header: 'Item Defect Partial' },
  { field: 'item_defect_complete', header: 'Item Defect Complete' },
  { field: 'item_damage_partial', header: 'Item Damage Partial' },
  { field: 'item_damage_complete', header: 'Item Damage Complete' },
  { field: 'order_wrong_parcel', header: 'Order Wrong Parcel' },
  { field: 'electronics_nonworking_partial', header: 'Electronics Item Nonworking Partial' },
  { field: 'electronics_nonworking_complete', header: 'Electronics Item Nonworking Complete' },
  { field: 'missing', header: 'Missing' },
  { field: 'missing_part', header: 'Missing Part' },
  { field: 'item_mismatch_wrong_item', header: 'Item Mismatch Wrong Item Delivered' },
];

function kpis(overrides: Partial<Kpis> = {}): Kpis {
  return {
    total_skus: 1467,
    total_quantity: 8475,
    total_orders: 52300,
    total_complaints: 218,
    shopify_sales: 34154,
    shopify_sales_pct: 4.7,
    shopify_sales_all: 721407,
    revenue_paise: 842000000,
    low_stock: 38,
    low_stock_threshold: 10,
    days: 30,
    stale: false,
    syncing: false,
    last_computed_at: '2026-07-30T09:40:00Z',
    ...overrides,
  };
}

function trend(): Trend {
  return {
    days: 30,
    points: [
      { day: '2026-07-28', units: 180, revenue_paise: 1800000 },
      { day: '2026-07-29', units: 90, revenue_paise: 900000 },
    ],
    previous: [
      { day: '2026-06-28', units: 100, revenue_paise: 1000000 },
      { day: '2026-06-29', units: 140, revenue_paise: 1400000 },
    ],
  };
}

function row(overrides: Partial<PerformanceRow> = {}): PerformanceRow {
  return {
    sku: 'DD-1001',
    sku_normalized: 'dd1001',
    shopify_sales: 512,
    shopify_sales_pct: 3.4,
    total_orders: 480,
    total_qty: 530,
    total_count: 610,
    complaints: {
      item_defect_partial: 1,
      item_defect_complete: 2,
      item_damage_partial: 3,
      item_damage_complete: 4,
      order_wrong_parcel: 5,
      electronics_nonworking_partial: 6,
      electronics_nonworking_complete: 7,
      missing: 8,
      missing_part: 9,
      item_mismatch_wrong_item: 10,
    },
    total_complaints: 55,
    status: 'critical',
    ...overrides,
  };
}

function overview(overrides: Partial<AnalyticsOverview> = {}): AnalyticsOverview {
  return { kpis: kpis(), trend: trend(), has_data: true, ...overrides };
}

const DISCONNECTED: ConnectionState = { connected: false, connection: null, source: 'none' };

const CONNECTED: ConnectionState = {
  connected: true,
  source: 'database',
  connection: {
    id: 1,
    shop_domain: 'deodap.myshopify.com',
    store_name: 'Deodap Retail',
    plan_name: 'Shopify Plus',
    currency: 'INR',
    token_scopes: 'read_orders',
    order_lookback_days: 90,
    status: 'connected',
    connected_at: '2026-07-28T10:00:00Z',
    disconnected_at: null,
    last_verified_at: '2026-07-28T10:00:00Z',
    store_latest_order_at: null,
    freshness_checked_at: null,
  },
};

const SUMMARY: SalesSummary = {
  orders: 385804,
  line_items: 512377,
  skus_with_sales: 1467,
  last_synced_at: '2026-07-30T09:00:00Z',
};

/** A finished run, as `/shopify/sync` reports it between syncs. */
function finishedRun(overrides: Partial<SyncRun> = {}): SyncRun {
  return {
    id: 10,
    trigger: 'manual',
    status: 'finished',
    stage: 'done',
    orders_pct: 100,
    orders_synced: 12000,
    line_items_synced: 18000,
    result: 'success',
    error_code: null,
    error_detail: null,
    retry_after_seconds: null,
    started_at: '2026-07-30T08:00:00Z',
    finished_at: '2026-07-30T09:00:00Z',
    duration_ms: 3600000,
    is_running: false,
    ...overrides,
  };
}

type Route = { ok: boolean; status: number; body: unknown };

/** Complaints imported with dates on them: they follow the range, no caveat. */
const DATED_SCOPE = {
  filtered_by_date: true,
  dated_skus: 1,
  undated_skus: 0,
  undated_complaints: 0,
};

/** An aggregated sheet: no Complaint Date column anywhere in the workspace. */
const UNDATED_SCOPE = {
  filtered_by_date: false,
  dated_skus: 0,
  undated_skus: 1,
  undated_complaints: 55,
};

/** Both kinds at once — what a store has after importing twice. */
const MIXED_SCOPE = {
  filtered_by_date: true,
  dated_skus: 308,
  undated_skus: 883,
  undated_complaints: 5456,
};

function routes(overrides: Record<string, Route> = {}) {
  const defaults: Record<string, Route> = {
    'GET /analytics/overview': { ok: true, status: 200, body: overview() },
    'GET /analytics/performance': {
      ok: true,
      status: 200,
      body: {
        rows: [row()],
        complaint_columns: COMPLAINT_COLUMNS,
        complaint_scope: DATED_SCOPE,
        total: 1,
        limit: 50,
        offset: 0,
        days: 30,
      },
    },
    'GET /shopify/sync': {
      ok: true,
      status: 200,
      body: { running: false, run: null, last_synced_at: null },
    },
    'GET /shopify/connection': { ok: true, status: 200, body: DISCONNECTED },
    'GET /shopify/sales/summary': { ok: true, status: 200, body: SUMMARY },
  };
  const table = { ...defaults, ...overrides };
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const key = Object.keys(table)
      .sort((a, b) => b.length - a.length)
      .find((candidate) => {
        const [routeMethod = '', routePath = ''] = candidate.split(' ');
        return routeMethod === method && url.includes(routePath);
      });
    const route = (key ? table[key] : undefined) ?? { ok: true, status: 200, body: {} };
    return Promise.resolve({
      ok: route.ok,
      status: route.status,
      json: () => Promise.resolve(route.body),
    } as Response);
  });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ShopifyStatusProvider>
          <DashboardPage />
        </ShopifyStatusProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the six cards', () => {
  it('shows every one, formatted the Indian way', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    expect(await screen.findByText('Total SKUs')).toBeDefined();
    expect(screen.getByText('1,467')).toBeDefined();
    expect(screen.getByText('Total quantity')).toBeDefined();
    expect(screen.getByText('8,475')).toBeDefined();
    expect(screen.getByText('Shopify sales')).toBeDefined();
    expect(screen.getByText('34,154')).toBeDefined();
    expect(screen.getByText('Shopify sales %')).toBeDefined();
    expect(screen.getByText('4.70%')).toBeDefined();
    expect(screen.getByText('Total orders')).toBeDefined();
    expect(screen.getByText('52,300')).toBeDefined();
    expect(screen.getByText('Total complaints')).toBeDefined();
    expect(screen.getByText('218')).toBeDefined();
  });

  it('states the denominator behind the percentage', async () => {
    /** A share with an invisible denominator is a number nobody can check. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    expect(await screen.findByText('of 7,21,407 units sold')).toBeDefined();
  });

  it('shows nothing the catalogue used to supply', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Total SKUs');
    for (const gone of ['Variants', 'Vendor', 'Sell-through', 'Out of stock', 'Products']) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it('tints the complaints card only when there are complaints', async () => {
    vi.stubGlobal('fetch', routes());
    const { container, unmount } = renderPage();
    await screen.findByText('218');
    const warned = container.querySelector('.kpi.warn');
    expect(warned?.textContent).toContain('Total complaints');
    unmount();

    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/overview': {
          ok: true,
          status: 200,
          body: overview({ kpis: kpis({ total_complaints: 0 }) }),
        },
      }),
    );
    const clean = renderPage();
    await screen.findByText('Total complaints');
    expect(clean.container.querySelector('.kpi.warn')).toBeNull();
  });
});

describe('the SKU table', () => {
  it('shows the same seventeen columns SKU Performance shows, in the same order', async () => {
    /**
     * Both pages render `SkuTable`, so this asserts the shared list rather than
     * a copy of it. A column that read differently here from how it reads there
     * would undermine both.
     */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByRole('columnheader', { name: 'SKU' });
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

  it('carries no Quantity, Total Qty or Stock column', async () => {
    /** Two quantity columns beside each other was the old table's own puzzle. */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByRole('columnheader', { name: 'SKU' });
    const headers = [...container.querySelectorAll('.tbl th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).not.toContain('Quantity');
    expect(headers).not.toContain('Total Qty');
    expect(headers).not.toContain('Stock');
  });

  it('puts each complaint value under its own column', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    const table = within(await screen.findByRole('table'));
    const cells = table.getAllByRole('cell').map((cell) => cell.textContent);
    // Six summary cells, then the ten categories in the order above: missing 8,
    // missing part 9, wrong item 10, wrong parcel 5, then defect/damage/electronics.
    expect(cells.slice(6, 16)).toEqual(['8', '9', '10', '5', '1', '2', '3', '4', '6', '7']);
  });

  it('ranks by total complaints, worst first', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await screen.findByRole('columnheader', { name: 'SKU' });
    await waitFor(() => {
      const asked = fetch.mock.calls
        .map(([input]) => String(input))
        .filter((url) => url.includes('/analytics/performance'));
      expect(asked.length).toBeGreaterThan(0);
      for (const url of asked) {
        expect(url).toContain('sort=total_complaints');
        expect(url).toContain('descending=true');
      }
    });
  });

  it('carries no Complaint Rate % column', async () => {
    /** The metric was removed from the project; the count stays. */
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByRole('columnheader', { name: 'SKU' });
    const headers = [...container.querySelectorAll('.tbl th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).toContain('Complaints');
    expect(headers.join(' ')).not.toContain('Complaint Rate');
  });

  it('debounces the SKU filter into a single request', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await screen.findByText('DD-1001');
    fetch.mockClear();
    await userEvent.type(screen.getByLabelText('Filter by SKU'), 'dd1001');

    await waitFor(() => {
      const searched = fetch.mock.calls
        .map((call) => String(call[0]))
        .filter((url) => url.includes('search='));
      expect(searched.length).toBe(1);
      expect(searched[0]).toContain('search=dd1001');
    });
  });
});

describe('states', () => {
  it('points at the import first when there is no sheet', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/overview': {
          ok: true,
          status: 200,
          body: overview({ has_data: false }),
        },
      }),
    );
    renderPage();

    expect(await screen.findByText('No data yet')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Import inventory' })).toBeDefined();
  });

  it('says the sales figures are behind rather than showing them as current', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/overview': {
          ok: true,
          status: 200,
          body: overview({ kpis: kpis({ stale: true }) }),
        },
      }),
    );
    renderPage();

    expect(await screen.findByText(/behind the last sync/)).toBeDefined();
  });

  it('shows the server’s own message when analytics fails', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/overview': {
          ok: false,
          status: 500,
          body: {
            error: {
              code: 'metrics_unavailable',
              message: 'The rollup could not be read.',
              next: 'Recompute the figures.',
            },
          },
        },
      }),
    );
    renderPage();

    expect(await screen.findByText('The rollup could not be read.')).toBeDefined();
  });
});

describe('the Shopify panel', () => {
  it('says no store is connected, and offers to connect one', async () => {
    /** The panel used to be absent entirely, so the dashboard said nothing
        about a store that was never connected. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    expect(await screen.findByText('No Shopify store connected')).toBeDefined();
    expect(screen.getByRole('button', { name: /Connect Shopify/ })).toBeDefined();
  });

  it('hides the sync figures when there is no store to have synced them', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('No Shopify store connected');
    expect(screen.queryByText('Orders synced')).toBeNull();
    expect(screen.queryByText('Line items synced')).toBeNull();
    expect(screen.queryByText('Last sync')).toBeNull();
  });

  it('shows the live store, last sync and counts once connected', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: {
            running: false,
            run: finishedRun(),
            last_synced_at: '2026-07-30T09:00:00Z',
          },
        },
      }),
    );
    renderPage();

    const panel = (await screen.findByText('Connected')).closest('.panel');
    expect(panel).not.toBeNull();
    const shopify = within(panel as HTMLElement);

    // The store name and domain, not a hardcoded placeholder.
    expect(shopify.getByText('Deodap Retail')).toBeDefined();
    expect(shopify.getByText('deodap.myshopify.com')).toBeDefined();

    // Counts come from the sales summary, in Indian grouping.
    expect(shopify.getByText('Orders synced')).toBeDefined();
    expect(shopify.getByText('3,85,804')).toBeDefined();
    expect(shopify.getByText('Line items synced')).toBeDefined();
    expect(shopify.getByText('5,12,377')).toBeDefined();

    expect(shopify.getByText('Last sync')).toBeDefined();
    // No "Sync now": a sync runs after every import, so the panel reports the
    // store rather than driving it. History is still one click away.
    expect(shopify.queryByRole('button', { name: /Sync now/ })).toBeNull();
    expect(shopify.getByRole('button', { name: /View sync history/ })).toBeDefined();
  });

  it('reports a running sync from its own counters, not the stored totals', async () => {
    /** Mid-run the summary is still the pre-run figure; the run is what is
        actually happening. */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: {
            running: true,
            run: finishedRun({
              status: 'running',
              stage: 'orders',
              result: null,
              is_running: true,
              orders_synced: 4200,
              line_items_synced: 6100,
              finished_at: null,
            }),
            last_synced_at: null,
          },
        },
      }),
    );
    renderPage();

    const panel = (await screen.findByText('Connected')).closest('.panel');
    const shopify = within(panel as HTMLElement);

    expect(shopify.getByText('4,200')).toBeDefined();
    expect(shopify.getByText('6,100')).toBeDefined();
    // The panel still says a run is in flight; there is just no button to press.
    expect(shopify.getAllByText(/Syncing…/).length).toBeGreaterThan(0);
    expect(shopify.queryByRole('button', { name: /Syncing…/ })).toBeNull();
  });

  it('does not claim a store is missing when the check itself failed', async () => {
    /** "Couldn't read it" and "there is none" are different answers, and only
        one of them means the user should go and connect a store. */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': {
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
    renderPage();

    expect(await screen.findByText(/Couldn.t check Shopify/)).toBeDefined();
    expect(screen.queryByText('No Shopify store connected')).toBeNull();
  });
});

describe('keeping itself current', () => {
  it('reloads the figures when a running sync finishes, without a page reload', async () => {
    /**
     * The acceptance criterion for the panel, and the reason the page no longer
     * calls `useSync(false)`: with polling disabled the completion was never
     * observed, so a sync could finish and every figure on screen stayed at its
     * pre-sync value until the user reloaded by hand.
     */
    let live = {
      running: true,
      run: finishedRun({
        status: 'running',
        result: null,
        is_running: true,
        orders_synced: 100,
        line_items_synced: 200,
      }),
      last_synced_at: null as string | null,
    };
    let summary: SalesSummary = { ...SUMMARY, orders: 1, line_items: 2 };

    const fetcher = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes('/shopify/sync')
        ? live
        : url.includes('/shopify/sales/summary')
          ? summary
          : url.includes('/shopify/connection')
            ? CONNECTED
            : url.includes('/analytics/overview')
              ? overview()
              : url.includes('/analytics/performance')
                ? {
                    rows: [row()],
                    complaint_columns: COMPLAINT_COLUMNS,
                    total: 1,
                    limit: 50,
                    offset: 0,
                    days: 30,
                  }
                : {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
      } as Response);
    });
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    // Mid-sync: the run's own climbing counter, not the stale stored total.
    expect(await screen.findByText('100')).toBeDefined();

    const overviewCalls = () =>
      fetcher.mock.calls.filter(([input]) => String(input).includes('/analytics/overview'))
        .length;
    const before = overviewCalls();

    // The sync finishes and the stored totals move.
    live = { running: false, run: finishedRun(), last_synced_at: '2026-07-30T09:00:00Z' };
    summary = { ...SUMMARY, orders: 7500, line_items: 9100 };

    // Picked up by the poll, with no interaction and no remount.
    await waitFor(() => expect(screen.getByText('7,500')).toBeDefined(), { timeout: 6000 });
    expect(screen.getByText('9,100')).toBeDefined();

    // And the analytics behind the cards were re-read, not just the panel.
    await waitFor(() => expect(overviewCalls()).toBeGreaterThan(before), { timeout: 6000 });
  });

  it('fetches the analytics once on arrival, not once per Shopify read', async () => {
    /**
     * The refresh signal starts at zero for exactly this reason: if simply
     * loading the shared Shopify status counted as a change, every visit to the
     * dashboard would fetch the overview and the SKU table twice.
     */
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await screen.findByText('1,467');
    // Settle anything the providers queued behind their first responses.
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([input]) => String(input).includes('/shopify/sales/summary')),
      ).toBe(true),
    );

    const calls = (path: string) =>
      fetcher.mock.calls.filter(([input]) => String(input).includes(path)).length;
    expect(calls('/analytics/overview')).toBe(1);
    expect(calls('/analytics/performance')).toBe(1);
  });
});

describe('the header actions', () => {
  const header = () => document.querySelector('.page-head .acts') as HTMLElement;

  it('offers Export snapshot, and neither Sync now nor Recompute', async () => {
    /**
     * Syncing moved to the import, which is the moment it is actually needed:
     * an import restates which SKUs matter and their sales have to catch up.
     * A button here asked the user to remember to do it.
     */
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Total SKUs');
    const acts = within(header());
    expect(acts.getByRole('button', { name: /Export snapshot/ })).toBeDefined();
    expect(acts.queryByRole('button', { name: /Sync now/ })).toBeNull();
    expect(acts.queryByRole('button', { name: /Recompute/ })).toBeNull();
  });

  it('lists the three formats the Export Centre already writes', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();
    await screen.findByText('Total SKUs');

    await userEvent.setup().click(screen.getByRole('button', { name: /Export snapshot/ }));

    const menu = within(document.querySelector('.pop.on') as HTMLElement);
    expect(menu.getByRole('menuitem', { name: 'CSV' })).toBeDefined();
    expect(menu.getByRole('menuitem', { name: 'Excel' })).toBeDefined();
    expect(menu.getByRole('menuitem', { name: 'PDF' })).toBeDefined();
  });

  it('exports through the existing reports endpoint, not one of its own', async () => {
    /**
     * The whole point of the requirement: no second export engine. If this
     * ever posts anywhere but /reports, the dashboard has grown one.
     */
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();
    await screen.findByText('Total SKUs');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Export snapshot/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Excel' }));

    const posted = fetcher.mock.calls.filter(
      ([input, init]) =>
        (init as RequestInit | undefined)?.method === 'POST' &&
        String(input).includes('/reports'),
    );
    await waitFor(() => expect(posted.length).toBeGreaterThan(0));
    const body = JSON.parse(String((posted[0]?.[1] as RequestInit).body)) as Record<
      string,
      unknown
    >;
    expect(body.kind).toBe('dashboard');
    expect(body.fmt).toBe('xlsx');
  });

  it('exports the range the cards are actually showing', async () => {
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();
    await screen.findByText('Total SKUs');

    const user = userEvent.setup();
    // Move the trend range picker off its default before exporting.
    await user.click(screen.getByRole('button', { name: '7D' }));
    await user.click(screen.getByRole('button', { name: /Export snapshot/ }));
    await user.click(screen.getByRole('menuitem', { name: 'CSV' }));

    const posted = fetcher.mock.calls.find(
      ([input, init]) =>
        (init as RequestInit | undefined)?.method === 'POST' &&
        String(input).includes('/reports'),
    );
    const body = JSON.parse(String((posted?.[1] as RequestInit).body)) as Record<
      string,
      unknown
    >;
    expect(body.range_option).toBe('7');
  });

  it('offers no sync control even while a run is in flight', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: {
            running: true,
            run: finishedRun({ status: 'running', result: null, is_running: true }),
            last_synced_at: null,
          },
        },
      }),
    );
    renderPage();
    await screen.findByText('Total SKUs');

    // Nothing to press. The page follows the run through the provider's change
    // signal and reloads its figures when it lands.
    expect(within(header()).queryByRole('button', { name: /Sync/ })).toBeNull();
    expect(within(header()).getByRole('button', { name: /Export snapshot/ })).toBeDefined();
  });

  it('offers a retry on the staleness banner, and never a Recompute', async () => {
    /**
     * Staleness now means one thing: the automatic recomputation failed. The
     * repair is to retry the sync — which reuses the orders already
     * downloaded — not to recompute by hand and not to re-import.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/overview': {
          ok: true,
          status: 200,
          body: overview({ kpis: kpis({ stale: true }) }),
        },
      }),
    );
    renderPage();

    // The match is the <b>; the rest of the sentence is its sibling.
    const bold = await screen.findByText(/behind the last sync/);
    expect(bold.parentElement?.textContent).toContain('retries the recompute alone');
    expect(screen.getByRole('button', { name: /Retry sync/ })).toBeDefined();
    expect(screen.queryByRole('button', { name: /Recompute/ })).toBeNull();
  });
});

describe('the SKU table is the top 50', () => {
  /** 50 rows out of a much larger workspace — the shape the cap exists for. */
  function bigTable(overrides: Record<string, unknown> = {}) {
    return {
      ok: true,
      status: 200,
      body: {
        rows: Array.from({ length: 50 }, (_, i) =>
          row({
            sku: `DD-${String(1000 + i)}`,
            // The table keys on the normalised SKU, so this has to vary too.
            sku_normalized: `dd${String(1000 + i)}`,
            shopify_sales: 5000 - i,
          }),
        ),
        complaint_columns: COMPLAINT_COLUMNS,
        total: 1641,
        limit: 50,
        offset: 0,
        days: 30,
        ...overrides,
      },
    };
  }

  it('asks for 50 and never for an offset', async () => {
    const fetcher = routes({ 'GET /analytics/performance': bigTable() });
    vi.stubGlobal('fetch', fetcher);
    renderPage();
    await screen.findByText('Total SKUs');

    const asked = fetcher.mock.calls
      .map(([input]) => String(input))
      .filter((url) => url.includes('/analytics/performance'));
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    for (const url of asked) {
      expect(url).toContain('limit=50');
      // No offset parameter at all: there is no page to be on but the first.
      expect(url).not.toContain('offset=');
    }
  });

  it('has no pager', async () => {
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    const { container } = renderPage();

    await screen.findByText('Total SKUs');
    const footer = container.querySelector('.tbl-ft') as HTMLElement;
    await waitFor(() => expect(footer).not.toBeNull());
    expect(within(footer).queryByRole('button', { name: 'Next' })).toBeNull();
    expect(within(footer).queryByRole('button', { name: 'Previous' })).toBeNull();
  });

  it('says what it is showing and what it is leaving out', async () => {
    /**
     * A list that quietly stops at 50 reads as "these are all your SKUs".
     * Removing the pager is the requirement; hiding the total would be a bug.
     */
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    renderPage();

    expect(await screen.findByText('Top 50 most complained of 1,641 SKUs')).toBeDefined();
  });

  it('offers the way through to every SKU', async () => {
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    renderPage();

    await screen.findByText('Total SKUs');
    expect(await screen.findByRole('button', { name: /See all SKUs/ })).toBeDefined();
  });

  it('labels the panel for the rows beneath it, not the workspace', async () => {
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    const { container } = renderPage();

    await screen.findByText('Total SKUs');
    const panel = [...container.querySelectorAll('.panel')].find(
      (el) => el.querySelector('h3')?.textContent === 'Top 50 Most Complained SKUs',
    ) as HTMLElement;
    expect(within(panel).getByText('Ranked by total complaints')).toBeDefined();
    // The old header claimed the workspace total beside a 50-row table.
    expect(within(panel).queryByText('1,641 rows')).toBeNull();
  });

  it('searches every SKU, not the fifty on screen', async () => {
    /** The filter is a server query, so a SKU ranked 1,400th is still findable. */
    const fetcher = routes({ 'GET /analytics/performance': bigTable() });
    vi.stubGlobal('fetch', fetcher);
    renderPage();
    await screen.findByText('Total SKUs');

    await userEvent.setup().type(screen.getByLabelText('Filter by SKU'), 'deep-sku');

    await waitFor(() =>
      expect(
        fetcher.mock.calls
          .map(([input]) => String(input))
          .some(
            (url) => url.includes('/analytics/performance') && url.includes('search=deep-sku'),
          ),
      ).toBe(true),
    );
  });

  it('counts matches rather than the top 50 while searching', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: {
            rows: [row({ sku: 'DD-1001' })],
            complaint_columns: COMPLAINT_COLUMNS,
            total: 1,
            limit: 50,
            offset: 0,
            days: 30,
          },
        },
      }),
    );
    renderPage();
    await screen.findByText('Total SKUs');

    await userEvent.setup().type(screen.getByLabelText('Filter by SKU'), 'DD-1001');

    expect(await screen.findByText('1 matching SKU')).toBeDefined();
    expect(screen.getByText('Matching your search')).toBeDefined();
  });

  it('leaves the KPI cards reading the whole database', async () => {
    /** Only the rows are capped. The cards are aggregates over everything. */
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    renderPage();

    await screen.findByText('Total SKUs');
    // 1,467 from the KPI fixture, not the 50 rows the table holds.
    expect(screen.getByText('1,467')).toBeDefined();
    expect(screen.getByText('8,475')).toBeDefined();
  });
});

describe('the trend chart states what it counts', () => {
  it('says the trend is the whole store and the card is not', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Shopify sales trend');
    expect(screen.getByText(/all Shopify sales/)).toBeDefined();
    expect(screen.getByText(/only SKUs imported into StockSync Analytics/)).toBeDefined();
  });
});

describe('the complaints table states whether the range applies to it', () => {
  it('confirms the range applies when the import carried complaint dates', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Top 50 Most Complained SKUs');
    expect(screen.getByText('Complaint totals follow the selected date range.')).toBeDefined();
    expect(screen.queryByText(/not filtered/)).toBeNull();
  });

  it('explains an aggregated import, whose totals ignore the range', async () => {
    /**
     * The table is headed "Top 50 Most Complained SKUs" beside a range control,
     * so a reader is entitled to assume the two are related. When the file had
     * no Complaint Date column they are not, and only this says so.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: {
            rows: [row()],
            complaint_columns: COMPLAINT_COLUMNS,
            complaint_scope: UNDATED_SCOPE,
            total: 1,
            limit: 50,
            offset: 0,
            days: 30,
          },
        },
      }),
    );
    renderPage();

    const note = await screen.findByText(/not filtered by date/);
    expect(note.textContent).toBe(
      'Complaint totals are not filtered by date because the imported file does not ' +
        'contain a Complaint Date column.',
    );
  });

  it('names the unfiltered remainder when the workspace holds both kinds', async () => {
    /**
     * A table headed "Top 50 Most Complained SKUs" beside a range control, where
     * 308 SKUs answer the range and 883 do not. Claiming no date column was
     * provided would be false, and reads as though the import lost the dates.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/performance': {
          ok: true,
          status: 200,
          body: {
            rows: [row()],
            complaint_columns: COMPLAINT_COLUMNS,
            complaint_scope: MIXED_SCOPE,
            total: 1,
            limit: 50,
            offset: 0,
            days: 30,
          },
        },
      }),
    );
    renderPage();

    const note = await screen.findByText(/Some imported complaint records/);
    expect(note.textContent).toBe(
      'Some imported complaint records follow the selected date range. The remaining 883 SKUs ' +
        '(5,456 complaints) were imported without Complaint Dates, so their complaint ' +
        'totals are not date-filtered.',
    );
    expect(screen.queryByText(/does not contain a Complaint Date column/)).toBeNull();
  });
});

describe('while a sync is running', () => {
  /**
   * A sync commits its orders page by page and recomputes at the end, so every
   * run passes through a moment where orders exist that the rollup has not
   * seen. Read as staleness that put "Sales figures are behind" on screen
   * mid-sync, beside a Retry the server would have refused with 409.
   */
  const syncing = () =>
    routes({
      'GET /analytics/overview': {
        ok: true,
        status: 200,
        body: overview({ kpis: kpis({ stale: false, syncing: true }) }),
      },
    });

  it('says a sync is in progress', async () => {
    vi.stubGlobal('fetch', syncing());
    renderPage();

    expect(await screen.findByText('Sync in progress…')).toBeDefined();
  });

  it('does not claim the figures are behind', async () => {
    vi.stubGlobal('fetch', syncing());
    renderPage();

    await screen.findByText('Sync in progress…');
    expect(screen.queryByText(/behind the last sync/)).toBeNull();
  });

  it('offers no Retry, because one is already running', async () => {
    vi.stubGlobal('fetch', syncing());
    renderPage();

    await screen.findByText('Sync in progress…');
    expect(screen.queryByRole('button', { name: /Retry sync/ })).toBeNull();
  });

  it('says it is working rather than warning', async () => {
    // Slate, not rust: in progress is information, not a problem.
    vi.stubGlobal('fetch', syncing());
    const { container } = renderPage();

    await screen.findByText('Sync in progress…');
    expect(container.querySelector('.inline-err.info')).not.toBeNull();
  });
});
