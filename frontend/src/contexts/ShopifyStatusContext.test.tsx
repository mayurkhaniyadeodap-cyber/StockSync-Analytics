// @vitest-environment jsdom
/**
 * One Shopify status, shared across the app.
 *
 * These two screens are mounted together on purpose. In the running app the
 * header and the dashboard sit in the shell while the Shopify page renders
 * inside it, so connecting a store on one has to be visible on the others
 * immediately — the provider is mounted once and never remounts on navigation,
 * so nothing would re-read the connection without an explicit signal.
 *
 * Also pins the sharing itself: three screens reading the same endpoints must
 * not mean three sets of requests.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ShopifyStatusProvider } from './ShopifyStatusContext';
import { ThemeProvider } from './ThemeContext';
import { ToastProvider } from './ToastContext';
import { AuthProvider } from './AuthContext';
import { Header } from '../components/shell/Header';
import { ShopifyPage } from '../pages/ShopifyPage';

const ME = {
  id: 1,
  email: 'admin@deodap.in',
  full_name: 'Administrator',
  initials: 'A',
  role: 'Admin',
  workspace: { id: 1, name: 'Deodap Retail', slug: 'deodap' },
  preferences: { theme: 'light', table_density: 'comfortable' },
};

const STORE = {
  id: 1,
  shop_domain: 'deodap.myshopify.com',
  store_name: 'Deodap Retail',
  plan_name: 'Shopify Plus',
  currency: 'INR',
  token_scopes: 'read_orders',
  order_lookback_days: 90,
  status: 'connected',
  connected_at: '2026-07-31T10:00:00Z',
  disconnected_at: null,
  last_verified_at: '2026-07-31T10:00:00Z',
  store_latest_order_at: null,
  freshness_checked_at: null,
};

const NOT_CONNECTED = { connected: false, connection: null, source: 'none' };
const CONNECTED = { connected: true, connection: STORE, source: 'database' };

function renderApp(fetcher: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetcher);
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <ShopifyStatusProvider>
              <Header onOpenNav={vi.fn()} />
              <ShopifyPage />
            </ShopifyStatusProvider>
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/**
 * A backend whose connection genuinely changes when one is saved, rather than
 * a fixed reply — the point of the test is that the *second* read differs.
 */
function backend() {
  let connection: unknown = NOT_CONNECTED;
  const calls: string[] = [];

  const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    calls.push(`${method} ${url.replace(/^.*\/api/, '')}`);

    let body: unknown = {};
    if (method === 'POST' && url.endsWith('/shopify/connection')) {
      connection = CONNECTED;
      body = CONNECTED;
    } else if (url.includes('/shopify/connection/test')) {
      body = {
        ok: true,
        profile: {
          shop_domain: STORE.shop_domain,
          store_name: STORE.store_name,
          plan_name: STORE.plan_name,
          currency: STORE.currency,
          scopes: ['read_orders'],
        },
      };
    } else if (url.includes('/shopify/connection')) {
      body = connection;
    } else if (url.includes('/auth/me')) {
      body = ME;
    } else if (url.includes('/shopify/sales/summary')) {
      body = { orders: 120, line_items: 300, skus_with_sales: 40, last_synced_at: null };
    } else if (url.includes('/shopify/syncs')) {
      body = { items: [], total: 0, limit: 50, offset: 0 };
    } else if (url.includes('/shopify/sync')) {
      body = { running: false, run: null, last_synced_at: null };
    } else if (url.includes('/shopify/freshness')) {
      body = {
        synced_through: null,
        store_latest_order_at: null,
        checked_at: null,
        behind: null,
        behind_seconds: null,
        behind_hours: null,
      };
    }

    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    } as Response);
  });

  return { fetcher, calls };
}

/** Fill the form and save, which is the only path that connects a store. */
async function connectAStore() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Store URL/i), 'deodap.myshopify.com');
  await user.type(screen.getByLabelText(/Admin API access token/i), 'example-token');
  await user.click(screen.getByRole('button', { name: /Save connection/i }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('connecting a store', () => {
  it('updates the header without a page reload', async () => {
    const { fetcher } = backend();
    renderApp(fetcher);

    // Before: the header says so, on both the pill and the bell.
    await waitFor(() => expect(screen.getByText('Not connected')).toBeDefined());

    await connectAStore();

    // After: the header re-read the connection itself. Nothing remounted, and
    // the user never navigated.
    await waitFor(() => expect(screen.queryByText('Not connected')).toBeNull());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Notifications' }));
    const bell = within(document.querySelector('.pop.notif') as HTMLElement);
    await waitFor(() => expect(bell.queryByText('Shopify not connected')).toBeNull());
  });

  it('names the live store in the header once it is connected', async () => {
    const { fetcher } = backend();
    renderApp(fetcher);
    await waitFor(() => expect(screen.getByText('Not connected')).toBeDefined());

    await connectAStore();
    await waitFor(() => expect(screen.queryByText('Not connected')).toBeNull());

    const user = userEvent.setup();
    await user.click(document.querySelector('.syncpill') as HTMLElement);
    const popover = within(document.querySelector('.pop.on') as HTMLElement);
    expect(popover.getByText('Deodap Retail')).toBeDefined();
  });
});

describe('sharing one status', () => {
  it('reads each Shopify endpoint once for the whole shell', async () => {
    /**
     * Three screens read the connection and the sync state. Before they shared
     * a provider each fetched its own, which is both wasteful and a way for two
     * panels to disagree about the same store.
     */
    const { fetcher, calls } = backend();
    renderApp(fetcher);

    await waitFor(() => expect(screen.getByText('Not connected')).toBeDefined());
    await waitFor(() =>
      expect(calls.some((call) => call.includes('/shopify/sales/summary'))).toBe(true),
    );

    const count = (path: string) =>
      calls.filter((call) => call.startsWith('GET ') && call.includes(path)).length;

    expect(count('/shopify/sync')).toBe(1);
    expect(count('/shopify/sales/summary')).toBe(1);
    // Two: the shared provider's, and the Shopify page's own, which drives the
    // form it alone renders. Not one per component that shows a status.
    expect(count('/shopify/connection')).toBe(2);
  });
});
