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
  AnalyticsInsights,
  AnalyticsOverview,
  ConnectionState,
  Kpis,
  PerformanceRow,
  RankedSku,
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

/** One row of a server-built ranking. */
function ranked(rank: number, overrides: Partial<RankedSku> = {}): RankedSku {
  return {
    rank,
    sku: `DD-200${String(rank)}`,
    sku_normalized: `dd200${String(rank)}`,
    shopify_sales: 1000 - rank * 100,
    shopify_sales_pct: 10 - rank,
    total_complaints: 0,
    total_qty: 50 * rank,
    total_orders: 10,
    ...overrides,
  };
}

function overview(overrides: Partial<AnalyticsOverview> = {}): AnalyticsOverview {
  return { kpis: kpis(), trend: trend(), has_data: true, ...overrides };
}

/**
 * `/analytics/insights`, which the Complaint breakdown panel reads.
 *
 * Only the parts that panel touches are filled in with meaningful figures; the
 * rest of the payload is present because the endpoint sends it, not because
 * anything here asserts on it.
 */
function insights(overrides: Partial<AnalyticsInsights> = {}): AnalyticsInsights {
  return {
    kpis: {
      total_skus: 1467,
      total_qty: 8475,
      shopify_sales: 34154,
      shopify_sales_pct: 4.7,
      total_orders: 52300,
      total_complaints: 218,
      avg_sales_per_sku: 23.3,
      shopify_sales_all: 721407,
    },
    sales: {
      shopify_sales: 34154,
      shopify_sales_pct: 4.7,
      highest: null,
      lowest: null,
      top: [],
      distribution: [],
    },
    complaints: {
      total_complaints: 218,
      most_complained: null,
      skus_with_complaints: 64,
      top_skus: [],
      categories: [
        { field_name: 'missing', label: 'Missing', count: 120, share_pct: 55.05 },
        { field_name: 'missing_part', label: 'Missing Part', count: 98, share_pct: 44.95 },
      ],
    },
    rankings: { top_selling: [], lowest_selling: [], highest_complaint: [] },
    inventory: {
      high_stock_low_sales: [],
      low_stock_high_sales: [],
      zero_sales: [],
      most_complaints: [],
      median_qty: 12,
      median_sales: 8,
      zero_sales_total: 0,
    },
    quick: [],
    trend: trend(),
    complaint_columns: COMPLAINT_COLUMNS,
    complaint_scope: DATED_SCOPE,
    days: 30,
    has_data: true,
    stale: false,
    syncing: false,
    last_computed_at: '2026-07-30T09:40:00Z',
    ...overrides,
  };
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
    'GET /analytics/insights': { ok: true, status: 200, body: insights() },
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

/**
 * The panel headed `name`.
 *
 * The page carries several panels and two tables now, so `screen.getByRole`
 * queries that used to be unambiguous no longer are. Scoping by the heading is
 * how a test says which table it means.
 */
function panel(name: string): HTMLElement {
  const heading = [...document.querySelectorAll('.panel h3')].find(
    (h3) => h3.textContent === name,
  );
  if (!heading) throw new Error(`No panel headed "${name}"`);
  return heading.closest('.panel') as HTMLElement;
}

/** The six KPI cards, which no longer hold the only "1,467" on the page. */
function cards(): HTMLElement {
  return document.querySelector('.cardgrid') as HTMLElement;
}

/**
 * Waits for the figures to land.
 *
 * Scoped to the card grid rather than querying the page for "Total SKUs": that
 * string is now both a card label and the label inside the inventory donut, so
 * a bare text query matches two elements and throws.
 */
async function loaded(): Promise<void> {
  await waitFor(() => expect(within(cards()).getByText('Total SKUs')).toBeDefined());
}

/** The bottom table's headers, once it has loaded. */
async function performanceHeaders(): Promise<(string | undefined)[]> {
  await waitFor(() =>
    expect(panel('Product Performance').querySelector('.tbl')).not.toBeNull(),
  );
  return [...panel('Product Performance').querySelectorAll('.tbl th')].map((th) =>
    th.textContent?.trim(),
  );
}

/**
 * Only the requests the bottom table made.
 *
 * Three panels read `/analytics/performance` now — the table, the attention
 * list and the out-of-stock count — and only the table sends the shared sort.
 */
function tableCalls(fetcher: { mock: { calls: unknown[][] } }): string[] {
  return fetcher.mock.calls
    .map((call) => String(call[0]))
    .filter(
      (url) => url.includes('/analytics/performance') && url.includes('sort=total_complaints'),
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

    await loaded();
    const kpi = within(cards());
    expect(kpi.getByText('1,467')).toBeDefined();
    expect(kpi.getByText('Total Quantity')).toBeDefined();
    expect(kpi.getByText('8,475')).toBeDefined();
    expect(kpi.getByText('Shopify Sales')).toBeDefined();
    expect(kpi.getByText('34,154')).toBeDefined();
    expect(kpi.getByText('Total Orders')).toBeDefined();
    expect(kpi.getByText('52,300')).toBeDefined();
    expect(kpi.getByText('Total Complaints')).toBeDefined();
    expect(kpi.getByText('218')).toBeDefined();
    // 218 complaints against 52,300 orders. Both figures are the sheet's, which
    // is what makes the ratio meaningful.
    expect(kpi.getByText('Complaint Rate')).toBeDefined();
    expect(kpi.getByText('0.42%')).toBeDefined();
  });

  it('names the denominator behind the complaint rate', async () => {
    /** A rate whose whole is unstated is not a figure anyone can check. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    await loaded();
    // Both figures are now the sheet's own, so the denominator alone says it.
    // The Dashboard asks for `complaints=total`, which is what makes the two
    // comparable — see the request assertions below.
    expect(within(cards()).getByText(/of 52,300 orders in the sheet/)).toBeDefined();
  });

  it('shows no rate at all when there are no orders to divide by', async () => {
    /** Zero orders is not a complaint rate of zero — it is no rate. */
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /analytics/overview': {
          ok: true,
          status: 200,
          body: overview({ kpis: kpis({ total_orders: 0 }) }),
        },
      }),
    );
    renderPage();

    await screen.findByText('Complaint Rate');
    const kpi = within(cards());
    expect(kpi.getByText('—')).toBeDefined();
    expect(kpi.getByText('no orders in the sheet to measure against')).toBeDefined();
  });

  it('states the denominator behind the percentage', async () => {
    /** A share with an invisible denominator is a number nobody can check. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    // Folded into the Shopify sales card's own note, where the share sits beside
    // the count it is a share of rather than on a card of its own.
    await loaded();
    // The window is named on the card, because this is the only figure in the
    // row the range moves.
    expect(
      within(cards()).getByText('4.70% of 7,21,407 units sold · last 30 days'),
    ).toBeDefined();
  });

  it('shows nothing the catalogue used to supply', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await loaded();
    for (const gone of ['Variants', 'Vendor', 'Sell-through', 'Out of stock', 'Products']) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it('tints the complaints card only when there are complaints', async () => {
    vi.stubGlobal('fetch', routes());
    const { container, unmount } = renderPage();
    await screen.findByText('Total Complaints');
    const warned = container.querySelector('.kpi.warn');
    expect(warned?.textContent).toContain('Total Complaints');
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
    await screen.findByText('Total Complaints');
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
    renderPage();

    const headers = await performanceHeaders();
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
    renderPage();

    const headers = await performanceHeaders();
    expect(headers).not.toContain('Quantity');
    expect(headers).not.toContain('Total Qty');
    expect(headers).not.toContain('Stock');
  });

  it('puts each complaint value under its own column', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await performanceHeaders();
    const table = within(panel('Product Performance'));
    const cells = table.getAllByRole('cell').map((cell) => cell.textContent);
    // Six summary cells, then the ten categories in the order above: missing 8,
    // missing part 9, wrong item 10, wrong parcel 5, then defect/damage/electronics.
    expect(cells.slice(6, 16)).toEqual(['8', '9', '10', '5', '1', '2', '3', '4', '6', '7']);
  });

  it('ranks by total complaints, worst first', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await performanceHeaders();
    await waitFor(() => {
      const asked = tableCalls(fetch);
      expect(asked.length).toBeGreaterThan(0);
      for (const url of asked) expect(url).toContain('descending=true');
    });
  });

  it('asks the attention list for the server’s own worst-first order', async () => {
    /**
     * Ascending on `status` is critical, attention, good, excellent — the order
     * `services/insights.py` defines. The page does not re-derive which verdicts
     * are bad; it asks for them in the order the server ranks them.
     */
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await waitFor(() => {
      const asked = fetch.mock.calls
        .map(([input]) => String(input))
        .filter((url) => url.includes('sort=status'));
      expect(asked.length).toBe(1);
      expect(asked[0]).toContain('descending=false');
    });
  });

  it('carries no Complaint Rate % column', async () => {
    /** The metric was removed from the project; the count stays. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    const headers = await performanceHeaders();
    expect(headers).toContain('Complaints');
    expect(headers.join(' ')).not.toContain('Complaint Rate');
  });

  it('debounces the SKU filter into a single request', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await performanceHeaders();
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

    // Twice, deliberately: once in the page banner, and once inside Inventory
    // Health, whose totals ride on this payload.
    expect((await screen.findAllByText('The rollup could not be read.')).length).toBe(2);
  });

  it('tells the panel that depends on the failed read, rather than spinning', async () => {
    /**
     * Inventory Health reads its totals off the KPI payload. When that read
     * failed the panel used to render a skeleton and keep it up for ever, so a
     * failed request looked exactly like a slow one — the worst of both.
     */
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

    await screen.findAllByText('The rollup could not be read.');
    const health = within(panel('Inventory Health'));
    expect(health.getByRole('button', { name: /Retry/ })).toBeDefined();
    expect(health.queryByRole('img', { name: /stock level/ })).toBeNull();
  });
});

describe('the Shopify panel', () => {
  it('says no store is connected, and offers to connect one', async () => {
    /** The panel used to be absent entirely, so the dashboard said nothing
        about a store that was never connected. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    expect(await screen.findByText('No store connected')).toBeDefined();
    expect(screen.getByRole('button', { name: /Connect Shopify/ })).toBeDefined();
  });

  it('hides the sync figures when there is no store to have synced them', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('No store connected');
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

    // Two cards, because the connection and the sync fail independently: a
    // store can be connected and its last sync have failed.
    await screen.findByText('Connected');
    const store = within(panel('Shopify Connection'));
    const sync = within(panel('Sync Status'));

    // The store name and domain, not a hardcoded placeholder.
    expect(store.getByText('Deodap Retail')).toBeDefined();
    expect(store.getByText('deodap.myshopify.com')).toBeDefined();

    // Counts come from the sales summary, in Indian grouping.
    expect(sync.getByText('Orders synced')).toBeDefined();
    expect(sync.getByText('3,85,804')).toBeDefined();
    expect(sync.getByText(/5,12,377 line items/)).toBeDefined();
    expect(sync.getByText(/1,467 SKUs with sales/)).toBeDefined();

    expect(sync.getByText('Last sync')).toBeDefined();
    // No "Sync now": a sync runs after every import, so the cards report the
    // store rather than driving it. History is still one click away.
    expect(sync.queryByRole('button', { name: /Sync now/ })).toBeNull();
    expect(sync.getByRole('button', { name: /View Sync History/i })).toBeDefined();
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

    await screen.findByText('Connected');
    const sync = within(panel('Sync Status'));

    expect(sync.getByText('4,200')).toBeDefined();
    expect(sync.getByText(/6,100 line items/)).toBeDefined();
    // The card still says a run is in flight; there is just no button to press.
    expect(sync.getAllByText(/Syncing…/).length).toBeGreaterThan(0);
    expect(sync.queryByRole('button', { name: /Syncing…/ })).toBeNull();
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
    expect(screen.queryByText('No store connected')).toBeNull();
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
              : url.includes('/analytics/insights')
                ? insights()
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
    await screen.findByText('Connected');
    expect(within(panel('Sync Status')).getByText('100')).toBeDefined();

    const overviewCalls = () =>
      fetcher.mock.calls.filter(([input]) => String(input).includes('/analytics/overview'))
        .length;
    const before = overviewCalls();

    // The sync finishes and the stored totals move.
    live = { running: false, run: finishedRun(), last_synced_at: '2026-07-30T09:00:00Z' };
    summary = { ...SUMMARY, orders: 7500, line_items: 9100 };

    // Picked up by the poll, with no interaction and no remount.
    await waitFor(() => expect(within(panel('Sync Status')).getByText('7,500')).toBeDefined(), {
      timeout: 6000,
    });
    // The line-item and SKU totals moved into the card's summary line; the
    // orders figure is the one the card still states on its own.

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

    await loaded();
    // Settle anything the providers queued behind their first responses.
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([input]) => String(input).includes('/shopify/sales/summary')),
      ).toBe(true),
    );

    const calls = (path: string) =>
      fetcher.mock.calls.filter(([input]) => String(input).includes(path)).length;
    expect(calls('/analytics/overview')).toBe(1);
    expect(calls('/analytics/insights')).toBe(1);
    // Four panels read the performance endpoint: the table, the attention list,
    // and the two counts behind the inventory donut. Each asks once, not twice
    // — the low-stock threshold arriving must not re-trigger the other reads.
    expect(tableCalls(fetcher).length).toBe(1);
    expect(calls('/analytics/performance')).toBe(4);
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

    await loaded();
    const acts = within(header());
    expect(acts.getByRole('button', { name: /Export snapshot/ })).toBeDefined();
    expect(acts.queryByRole('button', { name: /Sync now/ })).toBeNull();
    expect(acts.queryByRole('button', { name: /Recompute/ })).toBeNull();
  });

  it('lists the three formats the Export Centre already writes', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();
    await loaded();

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
    await loaded();

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
    await loaded();

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
    await loaded();

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
    await loaded();

    await waitFor(() => expect(tableCalls(fetcher).length).toBeGreaterThan(0));
    for (const url of tableCalls(fetcher)) {
      expect(url).toContain('limit=50');
      // No offset parameter at all: there is no page to be on but the first.
      expect(url).not.toContain('offset=');
    }
  });

  it('has no pager', async () => {
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    const { container } = renderPage();

    await loaded();
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

    await loaded();
    expect(await screen.findByRole('button', { name: /See all SKUs/ })).toBeDefined();
  });

  it('labels the panel for the rows beneath it, not the workspace', async () => {
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    renderPage();

    await loaded();
    const table = within(panel('Product Performance'));
    expect(table.getByText('Ranked by total complaints')).toBeDefined();
    // The old header claimed the workspace total beside a 50-row table.
    expect(table.queryByText('1,641 rows')).toBeNull();
  });

  it('searches every SKU, not the fifty on screen', async () => {
    /** The filter is a server query, so a SKU ranked 1,400th is still findable. */
    const fetcher = routes({ 'GET /analytics/performance': bigTable() });
    vi.stubGlobal('fetch', fetcher);
    renderPage();
    await loaded();

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
    await loaded();

    await userEvent.setup().type(screen.getByLabelText('Filter by SKU'), 'DD-1001');

    expect(await screen.findByText('1 matching SKU')).toBeDefined();
    expect(screen.getByText('Matching your filters')).toBeDefined();
  });

  it('leaves the KPI cards reading the whole database', async () => {
    /** Only the rows are capped. The cards are aggregates over everything. */
    vi.stubGlobal('fetch', routes({ 'GET /analytics/performance': bigTable() }));
    renderPage();

    await loaded();
    // 1,467 from the KPI fixture, not the 50 rows the table holds.
    const kpi = within(cards());
    expect(kpi.getByText('1,467')).toBeDefined();
    expect(kpi.getByText('8,475')).toBeDefined();
  });
});

describe('the trend chart states what it counts', () => {
  it('says the trend is the whole store and the card is not', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Shopify Sales Trend');
    expect(screen.getByText(/all Shopify sales/)).toBeDefined();
    expect(screen.getByText(/only SKUs imported into StockSync Analytics/)).toBeDefined();
  });
});

describe('the complaints table states whether the range applies to it', () => {
  it('confirms the range applies when the import carried complaint dates', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Product Performance');
    expect(screen.getByText('Complaint totals follow the selected date range.')).toBeDefined();
    expect(screen.queryByText(/not filtered/)).toBeNull();
  });

  it('explains an aggregated import, whose totals ignore the range', async () => {
    /**
     * The table is headed "Product Performance" beside a range control,
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
     * A table headed "Product Performance" beside a range control, where
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

describe('what the date range is allowed to move', () => {
  /**
   * Only Shopify Sales and Shopify Sales %. Everything else on this page is a
   * snapshot of the newest import, and a complaint total that moved with the
   * range while the order count beside it did not made Complaint Rate a ratio
   * of two different periods.
   */

  it('asks for complaints as the sheet’s whole record, not a slice', async () => {
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await loaded();
    await waitFor(() => {
      const asked = fetcher.mock.calls
        .map(([input]) => String(input))
        .filter(
          (url) =>
            url.includes('/analytics/overview') ||
            url.includes('/analytics/insights') ||
            url.includes('sort=total_complaints'),
        );
      expect(asked.length).toBeGreaterThan(0);
      for (const url of asked) expect(url).toContain('complaints=total');
    });
  });

  it('reads the stock split over the whole rollup, not the chosen range', async () => {
    /**
     * Inventory Health is a snapshot panel. Deciding "has this SKU ever sold?"
     * from a 7-day window made two of its four slices swing while the other two
     * sat still.
     */
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await loaded();
    await waitFor(() => {
      const counts = fetcher.mock.calls
        .map(([input]) => String(input))
        .filter((url) => url.includes('limit=1'));
      expect(counts.length).toBe(2);
      for (const url of counts) expect(url).toContain('days=365');
    });
  });

  it('still bounds the sales figures by the chosen range', async () => {
    /** The point of the change is that it moves complaints and leaves this
        alone. */
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await loaded();
    const overviewCall = fetcher.mock.calls
      .map(([input]) => String(input))
      .find((url) => url.includes('/analytics/overview'));
    expect(overviewCall).toContain('days=30');
  });

  it('says on the page which figures the range touches', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await loaded();
    expect(screen.getByText(/does not move with it/)).toBeDefined();
  });

  it('asks Products Requiring Attention for the whole record too', async () => {
    /**
     * That panel is ranked by the server's `status`, which is computed from the
     * complaint count — so the basis decides what it shows, not merely how the
     * figure reads. Under `range`, a SKU whose complaints all fall outside the
     * window classifies as `excellent` and drops out of the one table whose
     * whole job is to surface it.
     *
     * Pinned separately from the request above because that one matches the SKU
     * table's `sort=total_complaints`; this panel sends `sort=status` and is a
     * different call.
     */
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await loaded();
    await waitFor(() => {
      const attention = fetcher.mock.calls
        .map(([input]) => String(input))
        .filter((url) => url.includes('sort=status'));
      expect(attention.length).toBeGreaterThan(0);
      for (const url of attention) expect(url).toContain('complaints=total');
    });
  });
});

/**
 * The complaint rate, wherever it is shown.
 *
 * Two screens carry it — the card and the Rate column in Products Requiring
 * Attention — and both can legitimately compute more than 100%: one order can
 * draw several complaints, and the date range moves the numerator while the
 * order count stays a snapshot of the newest import. The ratio is real; the
 * reported figure stops at 100%, because a percentage past it reads as a
 * broken number rather than a large one.
 */
describe('the complaint rate never exceeds 100%', () => {
  const withKpis = (over: Partial<Kpis>) =>
    routes({
      'GET /analytics/overview': {
        ok: true,
        status: 200,
        body: overview({ kpis: kpis(over) }),
      },
    });

  const withRows = (...rows: PerformanceRow[]) =>
    routes({
      'GET /analytics/performance': {
        ok: true,
        status: 200,
        body: {
          rows,
          complaint_columns: COMPLAINT_COLUMNS,
          complaint_scope: DATED_SCOPE,
          total: rows.length,
          limit: 50,
          offset: 0,
        },
      },
    });

  it('caps the card at 100% when complaints outnumber orders', async () => {
    vi.stubGlobal('fetch', withKpis({ total_complaints: 250, total_orders: 80 }));
    renderPage();

    await loaded();
    // 312.5% before the cap.
    expect(within(cards()).getByText('100.00%')).toBeDefined();
    expect(within(cards()).queryByText('312.50%')).toBeNull();
  });

  it('shows exactly 100% when every order drew one complaint', async () => {
    vi.stubGlobal('fetch', withKpis({ total_complaints: 80, total_orders: 80 }));
    renderPage();

    await loaded();
    expect(within(cards()).getByText('100.00%')).toBeDefined();
  });

  it('leaves a rate below the cap alone', async () => {
    vi.stubGlobal('fetch', withKpis({ total_complaints: 20, total_orders: 80 }));
    renderPage();

    await loaded();
    expect(within(cards()).getByText('25.00%')).toBeDefined();
  });

  it('caps the Rate column in Products Requiring Attention', async () => {
    vi.stubGlobal(
      'fetch',
      withRows(row({ total_complaints: 55, total_orders: 8, status: 'critical' })),
    );
    renderPage();

    await loaded();
    // 687.5% before the cap.
    const attention = within(panel('Products Requiring Attention'));
    expect(await attention.findByText('100.00%')).toBeDefined();
  });

  it('shows no rate at all in that column when the SKU has no orders', async () => {
    /** Zero orders is not a rate of zero, and dividing by it is not a number. */
    vi.stubGlobal(
      'fetch',
      withRows(row({ total_complaints: 55, total_orders: 0, status: 'critical' })),
    );
    renderPage();

    await loaded();
    const attention = within(panel('Products Requiring Attention'));
    expect(await attention.findByText('—')).toBeDefined();
    expect(attention.queryByText('0.00%')).toBeNull();
    expect(attention.queryByText(/NaN/)).toBeNull();
  });

  it('leaves a per-SKU rate below the cap alone', async () => {
    vi.stubGlobal(
      'fetch',
      withRows(row({ total_complaints: 12, total_orders: 480, status: 'critical' })),
    );
    renderPage();

    await loaded();
    const attention = within(panel('Products Requiring Attention'));
    expect(await attention.findByText('2.50%')).toBeDefined();
  });
});

describe('Best-Selling Products', () => {
  /** Top imported SKUs by units sold, for the sheet that was just imported. */
  function withSellers() {
    return routes({
      'GET /analytics/insights': {
        ok: true,
        status: 200,
        body: insights({
          rankings: {
            top_selling: [ranked(1), ranked(2), ranked(3)],
            lowest_selling: [],
            highest_complaint: [],
          },
        }),
      },
    });
  }

  it('shows the SKU, its stock, its units sold and its share', async () => {
    /** All four on one row, because "it sold 900 units" and "there are 50
        left" are the same decision. */
    vi.stubGlobal('fetch', withSellers());
    renderPage();

    await loaded();
    const best = within(panel('Best-Selling Products'));
    await waitFor(() => expect(best.getByText('DD-2001')).toBeDefined());

    const headers = [...panel('Best-Selling Products').querySelectorAll('th')].map((th) =>
      th.textContent?.trim(),
    );
    expect(headers).toEqual([
      'Rank',
      'SKU',
      'Available Stock',
      'Shopify Sales',
      'Shopify Sales %',
    ]);

    const first = panel('Best-Selling Products').querySelectorAll('tbody tr')[0];
    const cells = [...(first?.querySelectorAll('td') ?? [])].map((td) =>
      td.textContent?.trim(),
    );
    expect(cells).toEqual(['1', 'DD-2001', '50', '900', '9.00%']);
  });

  it('keeps the server’s order, highest sales first', async () => {
    /** The ranking is the server's; re-sorting here would be a second opinion
        about "best selling", and Sales Analytics and every export use the
        first one. */
    vi.stubGlobal('fetch', withSellers());
    renderPage();

    await loaded();
    await waitFor(() =>
      expect(within(panel('Best-Selling Products')).getByText('DD-2001')).toBeDefined(),
    );

    const sold = [...panel('Best-Selling Products').querySelectorAll('tbody tr')].map((tr) =>
      Number((tr.querySelectorAll('td')[3]?.textContent ?? '0').replace(/,/g, '')),
    );
    expect(sold).toEqual([...sold].sort((a, b) => b - a));
  });

  it('says so when nothing has sold, rather than listing zeroes', async () => {
    /** The server drops rows that sold nothing, so an empty list is the honest
        state of a store whose sync has not matched the sheet yet. */
    vi.stubGlobal('fetch', routes());
    renderPage();

    await loaded();
    await waitFor(() =>
      expect(
        within(panel('Best-Selling Products')).getByText(/has sold a unit in this window/),
      ).toBeDefined(),
    );
  });

  it('costs no request of its own', async () => {
    /** It reads the insights payload the page already fetches for the
        complaint breakdown. */
    const fetcher = withSellers();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await loaded();
    await waitFor(() =>
      expect(within(panel('Best-Selling Products')).getByText('DD-2001')).toBeDefined(),
    );
    expect(
      fetcher.mock.calls.filter(([input]) => String(input).includes('/analytics/insights'))
        .length,
    ).toBe(1);
  });
});

describe('Needs Restocking', () => {
  function withRestock(zeroSales = 0) {
    return routes({
      'GET /analytics/insights': {
        ok: true,
        status: 200,
        body: insights({
          inventory: {
            high_stock_low_sales: [],
            low_stock_high_sales: [ranked(1, { total_qty: 4, shopify_sales: 880 })],
            zero_sales: [],
            most_complaints: [],
            median_qty: 12,
            median_sales: 8,
            zero_sales_total: zeroSales,
          },
        }),
      },
    });
  }

  it('names the cuts it was measured against', async () => {
    /** "Low stock" against a constant is wrong for every store but the one the
        constant was written for, so the list states the medians it used. */
    vi.stubGlobal('fetch', withRestock());
    renderPage();

    await loaded();
    await waitFor(() =>
      expect(
        within(panel('Needs Restocking')).getByText(/Under 12 units held, over 8 sold/),
      ).toBeDefined(),
    );
  });

  it('reports stock that is not moving, from the other end', async () => {
    vi.stubGlobal('fetch', withRestock(37));
    renderPage();

    await loaded();
    await waitFor(() =>
      expect(
        within(panel('Needs Restocking')).getByText(/37 SKUs hold stock and sold nothing/),
      ).toBeDefined(),
    );
  });

  it('says nothing needs reordering when nothing does', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await loaded();
    await waitFor(() =>
      expect(
        within(panel('Needs Restocking')).getByText(/Nothing here needs reordering/),
      ).toBeDefined(),
    );
  });
});
