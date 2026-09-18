// @vitest-environment jsdom
/**
 * The header's notifications and sync pill.
 *
 * Worth a test because both used to be fixed text: the bell always said
 * "You're all caught up." and the pill was passed a hardcoded `syncState="ok"`,
 * so the app claimed to be synced with no store connected and stayed silent
 * when a sync had actually failed. A reassuring lie is worse than no
 * notification area at all, and only a test keeps one from creeping back.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Header } from './Header';
import { AuthProvider } from '../../contexts/AuthContext';
import { ShopifyStatusProvider } from '../../contexts/ShopifyStatusContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import type { ConnectionState, SalesSummary, SyncRun } from '../../types/api';

const ME = {
  id: 1,
  email: 'admin@deodap.in',
  full_name: 'Administrator',
  initials: 'A',
  role: 'Admin',
  workspace: { id: 1, name: 'Deodap Retail', slug: 'deodap' },
  preferences: { theme: 'light', table_density: 'comfortable' },
};

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

function run(overrides: Partial<SyncRun> = {}): SyncRun {
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

function routes(overrides: Record<string, Route> = {}) {
  const table: Record<string, Route> = {
    'GET /auth/me': { ok: true, status: 200, body: ME },
    'GET /shopify/connection': { ok: true, status: 200, body: DISCONNECTED },
    'GET /shopify/sales/summary': { ok: true, status: 200, body: SUMMARY },
    'GET /shopify/sync': {
      ok: true,
      status: 200,
      body: { running: false, run: null, last_synced_at: null },
    },
    ...overrides,
  };
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

/** Where the router ended up, as text a test can read. */
function Where() {
  const { pathname } = useLocation();
  return <output data-testid="where">{pathname}</output>;
}

const where = () => screen.getByTestId('where').textContent;

function renderHeader() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <ShopifyStatusProvider>
              <Header onOpenNav={vi.fn()} />
              <Where />
            </ShopifyStatusProvider>
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** Open the bell and hand back a scope limited to its dropdown. */
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the sync pill', () => {
  it('does not claim "Synced" with no store connected', async () => {
    vi.stubGlobal('fetch', routes());
    renderHeader();

    await waitFor(() => expect(screen.getByText('Not connected')).toBeDefined());
    expect(screen.queryByText('Synced')).toBeNull();
  });

  it('shows the live store name and when it last synced', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: { running: false, run: run(), last_synced_at: '2026-07-30T09:00:00Z' },
        },
      }),
    );
    renderHeader();

    await waitFor(() => expect(screen.getByText('Synced')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Synced/ }));

    // Scoped to the popover: the workspace name in the logo is also "Deodap
    // Retail", and matching that would prove nothing about the store.
    const popover = within(document.querySelector('.pop.on') as HTMLElement);
    expect(popover.getByText('Deodap Retail')).toBeDefined();
    expect(popover.getByText(/^Synced .* ago$/)).toBeDefined();
    expect(popover.queryByText('No store connected yet')).toBeNull();
  });

  it('turns red when the last sync failed', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: { running: false, run: run({ result: 'failed' }), last_synced_at: null },
        },
      }),
    );
    renderHeader();

    await waitFor(() => expect(screen.getByText('Sync failed')).toBeDefined());
  });
});

describe('what the header carries, and what it does not', () => {
  /**
   * Two things have been tried up here and taken back out — a global search box
   * and the date range. Asserting their absence is worth the lines because each
   * was added in good faith and each turned out to belong somewhere else; a bar
   * that accumulates controls is how it happens.
   */

  it('has no search box', async () => {
    /**
     * Removed once for being a placeholder that returned nothing, added back
     * when it was wired to SKU Performance, and gone again: that page's own
     * filter runs the same query beside the status, complaint and quantity
     * filters that make a SKU search useful.
     */
    vi.stubGlobal('fetch', routes());
    renderHeader();

    await screen.findByRole('button', { name: /Administrator|Log out|StockSync/ });
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it('has no notifications bell', async () => {
    /**
     * The dropdown listed what the Shopify status provider already surfaces on
     * the pages themselves — "no store connected" on the Shopify card, a failed
     * sync on the Sync Status card and in Sync History. A second, quieter copy
     * behind a bell was a place for those to be missed rather than seen.
     */
    vi.stubGlobal('fetch', routes());
    const { container } = renderHeader();

    await screen.findByRole('button', { name: /Administrator|Log out|StockSync/ });
    expect(screen.queryByRole('button', { name: /Notifications/ })).toBeNull();
    expect(container.querySelector('.dot-count')).toBeNull();
  });

  it('has no date range', async () => {
    /**
     * It belongs on the pages that draw the figures, where the control sits
     * beside the chart it changes. The range is still shared across them — the
     * context outlived the control that used to set it — so moving from the
     * Dashboard to Sales Analytics still keeps the window you chose.
     */
    vi.stubGlobal('fetch', routes());
    const { container } = renderHeader();

    await screen.findByRole('button', { name: /Administrator|Log out|StockSync/ });
    expect(screen.queryByRole('button', { name: /Last \d+ days/ })).toBeNull();
    expect(container.querySelector('.hdr-range-wrap')).toBeNull();
  });

  it('does carry the brand, which is the one thing that stayed', async () => {
    /**
     * It sits here rather than on the rail, because the header spans the full
     * width *above* the rail — a lockup in both put two identical ones thirty
     * pixels apart. Here it also survives the rail collapsing to 72px and the
     * drawer closing on a phone.
     */
    vi.stubGlobal('fetch', routes());
    renderHeader();

    await screen.findByRole('button', { name: /Administrator|Log out|StockSync/ });
    expect(
      screen.getByRole('button', { name: 'StockSync Analytics — go to dashboard' }),
    ).toBeDefined();
  });

  it('takes the brand back to the dashboard', async () => {
    vi.stubGlobal('fetch', routes());
    renderHeader();

    await userEvent
      .setup()
      .click(await screen.findByRole('button', { name: /StockSync Analytics/ }));

    expect(where()).toBe('/dashboard');
  });

  it('keeps the controls it does carry, right-aligned', async () => {
    /** .hdr-r carries margin-left:auto, so alignment survives everything above
        being taken out of the bar. */
    vi.stubGlobal('fetch', routes());
    const { container } = renderHeader();

    await screen.findByRole('button', { name: /Administrator|Log out|StockSync/ });
    expect(container.querySelector('.hdr-r')).not.toBeNull();
    expect(container.querySelector('.syncpill')).not.toBeNull();
    expect(container.querySelector('.usermenu-btn')).not.toBeNull();
  });
});
