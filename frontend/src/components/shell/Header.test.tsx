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
import { MemoryRouter } from 'react-router-dom';
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

function renderHeader() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <ShopifyStatusProvider>
              <Header onOpenNav={vi.fn()} />
            </ShopifyStatusProvider>
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** Open the bell and hand back a scope limited to its dropdown. */
async function openNotifications() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Notifications' }));
  const panel = document.querySelector('.pop.notif');
  expect(panel).not.toBeNull();
  return within(panel as HTMLElement);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('notifications', () => {
  it('says the store is not connected instead of "all caught up"', async () => {
    vi.stubGlobal('fetch', routes());
    renderHeader();

    const notifications = await openNotifications();
    await waitFor(() => expect(notifications.getByText('Shopify not connected')).toBeDefined());
    expect(notifications.queryByText(/all caught up/)).toBeNull();
  });

  it('reports a sync in progress', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: {
            running: true,
            run: run({ status: 'running', result: null, is_running: true }),
            last_synced_at: null,
          },
        },
      }),
    );
    renderHeader();

    const notifications = await openNotifications();
    await waitFor(() => expect(notifications.getByText('Sync in progress')).toBeDefined());
  });

  it('reports the last sync succeeding', async () => {
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

    const notifications = await openNotifications();
    await waitFor(() => expect(notifications.getByText('Last sync successful')).toBeDefined());
    expect(notifications.getByText('12,000 orders synced.')).toBeDefined();
  });

  it('reports a failed sync with the server’s own reason', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: {
            running: false,
            run: run({
              result: 'failed',
              error_code: 'shopify_unauthorized',
              error_detail: 'Shopify rejected the token.',
            }),
            last_synced_at: null,
          },
        },
      }),
    );
    renderHeader();

    const notifications = await openNotifications();
    await waitFor(() => expect(notifications.getByText('Last sync failed')).toBeDefined());
    // The server's sentence, not one invented in the browser.
    expect(notifications.getByText('Shopify rejected the token.')).toBeDefined();
  });

  it('says new orders are waiting when the store is ahead of the last sync', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': {
          ok: true,
          status: 200,
          body: {
            ...CONNECTED,
            connection: {
              ...CONNECTED.connection,
              // Recorded by the last sync, so this costs no Shopify request.
              store_latest_order_at: '2026-07-31T06:00:00Z',
            },
          },
        },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: { running: false, run: run(), last_synced_at: '2026-07-30T09:00:00Z' },
        },
      }),
    );
    renderHeader();

    const notifications = await openNotifications();
    await waitFor(() =>
      expect(notifications.getByText('New Shopify orders available')).toBeDefined(),
    );
  });

  it('only says "all caught up" when every check actually passed', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: { running: false, run: null, last_synced_at: null },
        },
      }),
    );
    renderHeader();

    const notifications = await openNotifications();
    await waitFor(() => expect(notifications.getByText(/all caught up/)).toBeDefined());
  });
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

describe('the header has no global search', () => {
  it('renders no search box of its own', async () => {
    /**
     * It was a placeholder wired to nothing — an input that looked live and
     * returned nothing when you typed. Page-level search boxes are real and
     * unaffected; this only asserts the header carries none.
     */
    vi.stubGlobal('fetch', routes());
    renderHeader();

    await screen.findByRole('button', { name: 'Notifications' });
    expect(screen.queryByRole('searchbox')).toBeNull();
    expect(screen.queryByLabelText('Global search')).toBeNull();
  });

  it('keeps the right-hand controls where they were', async () => {
    /** .hdr-r carries margin-left:auto, so alignment survives the removal. */
    vi.stubGlobal('fetch', routes());
    const { container } = renderHeader();

    await screen.findByRole('button', { name: 'Notifications' });
    expect(container.querySelector('.hdr-r')).not.toBeNull();
    expect(container.querySelector('.mark')).not.toBeNull();
    expect(container.querySelector('.hdr .search')).toBeNull();
  });
});
