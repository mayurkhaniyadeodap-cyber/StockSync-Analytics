// @vitest-environment jsdom
/** Shopify connection (design doc §9). */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ShopifyPage } from './ShopifyPage';
import { ShopifyStatusProvider } from '../contexts/ShopifyStatusContext';
import { ToastProvider } from '../contexts/ToastContext';
import type { ShopifyConnection } from '../types/api';

const CONNECTED: ShopifyConnection = {
  id: 1,
  shop_domain: 'mystore.myshopify.com',
  store_name: 'Deodap Retail',
  plan_name: 'Shopify Plus',
  currency: 'INR',
  token_scopes: 'read_orders,read_all_orders',
  order_lookback_days: 90,
  status: 'connected',
  connected_at: '2026-07-28T10:00:00Z',
  disconnected_at: null,
  last_verified_at: '2026-07-28T10:00:00Z',
  store_latest_order_at: null,
  freshness_checked_at: null,
};

type Route = { ok: boolean; status: number; body: unknown };

/** Route fetch by "METHOD path" so a test states only what it cares about. */
const FALLBACK: Route = { ok: true, status: 200, body: {} };

/** A connected page also renders the sync panel and the sales summary, so their
 *  endpoints need a well-shaped default or the components see undefined. */
const DEFAULTS: Record<string, Route> = {
  'GET /shopify/sync': {
    ok: true,
    status: 200,
    body: { running: false, run: null, last_synced_at: null },
  },
  'GET /shopify/syncs': {
    ok: true,
    status: 200,
    body: { items: [], total: 0, limit: 50, offset: 0 },
  },
  'GET /shopify/sales/summary': {
    ok: true,
    status: 200,
    body: { orders: 0, line_items: 0, skus_with_sales: 0, last_synced_at: null },
  },
  'GET /shopify/freshness': {
    ok: true,
    status: 200,
    body: {
      synced_through: '2026-07-30T06:00:00Z',
      store_latest_order_at: '2026-07-30T06:05:00Z',
      checked_at: '2026-07-30T06:06:00Z',
      behind: false,
      behind_seconds: 300,
      behind_hours: 0.1,
    },
  },
};

function router(overrides: Record<string, Route>) {
  const routes = { ...DEFAULTS, ...overrides };
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    // Longest path first: '/shopify/syncs' must win over '/shopify/sync'.
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((candidate) => {
        const [routeMethod = '', routePath = ''] = candidate.split(' ');
        return routeMethod === method && url.includes(routePath);
      });
    const route = (key ? routes[key] : undefined) ?? FALLBACK;
    return Promise.resolve({
      ok: route.ok,
      status: route.status,
      json: () => Promise.resolve(route.body),
    } as Response);
  });
}

/** A connected store, with any field overridden for the case under test. */
function connected(overrides: Partial<ShopifyConnection> = {}): Route {
  return {
    ok: true,
    status: 200,
    body: {
      connected: true,
      source: 'database',
      connection: { ...CONNECTED, ...overrides },
    },
  };
}

const NOT_CONNECTED: Route = {
  ok: true,
  status: 200,
  body: { connected: false, connection: null, source: 'none' },
};

/** A store configured in .env: no row, so no id and nothing to disconnect. */
const FROM_ENV: Route = {
  ok: true,
  status: 200,
  body: {
    connected: true,
    source: 'environment',
    connection: {
      ...CONNECTED,
      id: null,
      shop_domain: 'envstore.myshopify.com',
      store_name: null,
      plan_name: null,
      currency: null,
      token_scopes: null,
      connected_at: null,
      last_verified_at: null,
    },
  },
};

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ShopifyStatusProvider>
          <ShopifyPage />
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

/**
 * Shopify's admin-token prefix, assembled rather than written as a literal.
 *
 * The assertions below are about the *shape*: they prove no Shopify-looking
 * token reaches the DOM. A neutral placeholder would leave them passing while
 * proving nothing, since any absent string satisfies `not.toContain`.
 *
 * A secret scanner cannot tell a fixture from a credential, and a test that
 * fails the scan on every push is a test somebody eventually deletes. Splitting
 * the literal keeps the scan quiet and the assertion exactly as strong.
 */
const SHOPIFY_TOKEN_PREFIX = 'shp' + 'at_';

describe('when no store is connected', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': NOT_CONNECTED }));
  });

  it('shows the empty state and the connect form', async () => {
    renderPage();

    expect(await screen.findByText('No store connected')).toBeDefined();
    expect(screen.getByLabelText(/Store URL/i)).toBeDefined();
    expect(screen.getByLabelText(/Admin API access token/i)).toBeDefined();
  });

  it('keeps both actions disabled until the form is filled', async () => {
    renderPage();
    await screen.findByText('No store connected');

    const test = screen.getByRole('button', { name: /Test connection/i });
    expect(test.hasAttribute('disabled')).toBe(true);

    await userEvent.type(screen.getByLabelText(/Store URL/i), 'mystore.myshopify.com');
    expect(test.hasAttribute('disabled')).toBe(true);

    await userEvent.type(screen.getByLabelText(/Admin API access token/i), 'example-token');
    expect(test.hasAttribute('disabled')).toBe(false);
  });

  it('masks the token field', async () => {
    renderPage();
    await screen.findByText('No store connected');

    expect(screen.getByLabelText(/Admin API access token/i).getAttribute('type')).toBe(
      'password',
    );
  });

  it('shows the store profile after a successful test', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': NOT_CONNECTED,
        'POST /shopify/connection/test': {
          ok: true,
          status: 200,
          body: {
            ok: true,
            profile: {
              shop_domain: 'mystore.myshopify.com',
              store_name: 'Deodap Retail',
              plan_name: 'Shopify Plus',
              currency: 'INR',
              scopes: ['read_products', 'read_orders'],
            },
          },
        },
      }),
    );

    renderPage();
    await screen.findByText('No store connected');
    await userEvent.type(screen.getByLabelText(/Store URL/i), 'mystore.myshopify.com');
    await userEvent.type(screen.getByLabelText(/Admin API access token/i), 'example-token');
    await userEvent.click(screen.getByRole('button', { name: /^Test connection$/i }));

    expect(await screen.findByText('Deodap Retail')).toBeDefined();
    expect(screen.getByText(/Connection verified/i)).toBeDefined();
  });

  it('shows the server message and next step when the token is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': NOT_CONNECTED,
        'POST /shopify/connection/test': {
          ok: false,
          status: 400,
          body: {
            error: {
              code: 'shopify_auth_failed',
              message: 'Connection failed — check the store URL and token permissions.',
              next: 'Confirm the Admin API access token is correct.',
            },
          },
        },
      }),
    );

    renderPage();
    await screen.findByText('No store connected');
    await userEvent.type(screen.getByLabelText(/Store URL/i), 'mystore.myshopify.com');
    await userEvent.type(screen.getByLabelText(/Admin API access token/i), 'bad');
    await userEvent.click(screen.getByRole('button', { name: /^Test connection$/i }));

    expect(await screen.findByText(/Connection failed/i)).toBeDefined();
    // §16: the recovery step travels with the error rather than being invented.
    expect(screen.getByText(/Confirm the Admin API access token is correct/i)).toBeDefined();
  });

  it('never sends the token in a URL', async () => {
    const fetchMock = router({
      'GET /shopify/connection': NOT_CONNECTED,
      'POST /shopify/connection/test': {
        ok: true,
        status: 200,
        body: {
          ok: true,
          profile: {
            shop_domain: 'mystore.myshopify.com',
            store_name: null,
            plan_name: null,
            currency: null,
            scopes: [],
          },
        },
      },
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPage();
    await screen.findByText('No store connected');
    await userEvent.type(screen.getByLabelText(/Store URL/i), 'mystore.myshopify.com');
    await userEvent.type(
      screen.getByLabelText(/Admin API access token/i),
      'example-secret-value',
    );
    await userEvent.click(screen.getByRole('button', { name: /^Test connection$/i }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.every((url) => !url.includes('example-secret-value'))).toBe(true);
    });
  });
});

describe('when a store is connected', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': {
          ok: true,
          status: 200,
          body: { connected: true, connection: CONNECTED, source: 'database' },
        },
      }),
    );
  });

  it('shows the store details rather than the form', async () => {
    renderPage();

    expect(await screen.findByText('mystore.myshopify.com')).toBeDefined();
    expect(screen.getByText('Deodap Retail')).toBeDefined();
    // The plan and the granted scopes went with the simplification: neither
    // says anything about whether your SKUs have sales behind them.
    expect(screen.queryByText(/Shopify Plus/)).toBeNull();
    expect(screen.queryByText('read_orders')).toBeNull();
    expect(screen.queryByLabelText(/Admin API access token/i)).toBeNull();
  });

  it('requires a second click to disconnect', async () => {
    renderPage();
    await screen.findByText('mystore.myshopify.com');

    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }));

    // Destructive and irreversible from the UI, so it arms first.
    expect(screen.getByRole('button', { name: /Confirm disconnect/i })).toBeDefined();
  });

  it('shows the expired state when the token stops working', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': {
          ok: true,
          status: 200,
          body: {
            connected: false,
            source: 'none',
            connection: { ...CONNECTED, status: 'token_expired' },
          },
        },
      }),
    );

    renderPage();

    // Not connected, so the reconnect form is offered with the domain kept.
    expect(await screen.findByLabelText(/Store URL/i)).toBeDefined();
    expect(screen.getByLabelText<HTMLInputElement>(/Store URL/i).value).toBe(
      'mystore.myshopify.com',
    );
  });
});

describe('when the store comes from .env', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': FROM_ENV }));
  });

  it('shows the configured store URL', async () => {
    renderPage();

    expect(await screen.findByText('envstore.myshopify.com')).toBeDefined();
  });

  it('says where the store came from', async () => {
    renderPage();

    expect(await screen.findByText('From .env')).toBeDefined();
  });

  it('offers no Disconnect, because there is no row to remove', async () => {
    renderPage();
    await screen.findByText('envstore.myshopify.com');

    expect(screen.queryByRole('button', { name: /Disconnect/i })).toBeNull();
  });

  it('carries no connect form, because a connected page has nothing to connect', async () => {
    renderPage();
    await screen.findByText('envstore.myshopify.com');

    expect(screen.queryByLabelText(/Store URL/i)).toBeNull();
    expect(screen.queryByText('No store connected')).toBeNull();
  });

  it('never renders a token', async () => {
    const { container } = renderPage();
    await screen.findByText('envstore.myshopify.com');

    expect(container.textContent).not.toContain(SHOPIFY_TOKEN_PREFIX);
  });
});

describe('store card', () => {
  /**
   * Four cells, and each answers a question about the connection. The page used
   * to also report orders pulled, the rolling lookback window, token scopes and
   * an hours-behind freshness comparison — Shopify's own bookkeeping on a page
   * about whether the store is connected and its sales current.
   */
  const summary = (body: unknown) => ({
    'GET /shopify/sales/summary': { ok: true, status: 200, body },
  });

  const synced = summary({
    orders: 1204,
    line_items: 7311,
    skus_with_sales: 892,
    last_synced_at: '2026-07-29T10:00:00Z',
  });

  it('names the store and its URL', async () => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': connected(), ...synced }));
    renderPage();

    expect(await screen.findByText('Store name')).toBeDefined();
    expect(screen.getByText('Deodap Retail')).toBeDefined();
    expect(screen.getByText('Store URL')).toBeDefined();
    expect(screen.getByText('mystore.myshopify.com')).toBeDefined();
  });

  it('says when the store was last pulled successfully', async () => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': connected(), ...synced }));
    renderPage();

    expect(await screen.findByText('Last successful sync')).toBeDefined();
    // Awaited: the timestamp arrives with the sales-summary fetch, a tick
    // after the cell itself renders with "Never".
    expect(await screen.findByText(/29 Jul/)).toBeDefined();
  });

  it('says Never rather than a blank when nothing has synced', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': connected(),
        ...summary({ orders: 0, line_items: 0, skus_with_sales: 0, last_synced_at: null }),
      }),
    );
    renderPage();

    expect(await screen.findByText('Never')).toBeDefined();
  });

  it('says a name is not reported rather than inventing one', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': {
          ok: true,
          status: 200,
          body: {
            connected: true,
            connection: { ...CONNECTED, store_name: null, plan_name: null },
            source: 'database',
          },
        },
        ...synced,
      }),
    );
    renderPage();

    expect(await screen.findByText('Not reported')).toBeDefined();
    expect(screen.getByText('Run Test connection to read it')).toBeDefined();
  });

  it('offers Test connection', async () => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': connected(), ...synced }));
    renderPage();

    expect(await screen.findByRole('button', { name: /Test connection/ })).toBeDefined();
  });

  it('shows no order count, lookback window, scopes or freshness', async () => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': connected(), ...synced }));
    renderPage();
    await screen.findByText('Store name');

    for (const gone of [
      /Orders pulled/,
      /Rolling/,
      /Orders up to date/,
      /Token scopes/,
      /read_orders/,
      /checked/i,
    ]) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });
});

describe('the sync status cell', () => {
  const summary = {
    'GET /shopify/sales/summary': {
      ok: true,
      status: 200,
      body: { orders: 1, line_items: 1, skus_with_sales: 1, last_synced_at: null },
    },
  };

  const syncState = (body: unknown) => ({
    'GET /shopify/sync': { ok: true, status: 200, body },
  });

  const run = (over: Record<string, unknown>) => ({
    id: 1,
    trigger: 'import',
    status: 'finished',
    stage: 'done',
    orders_pct: 100,
    orders_synced: 400,
    line_items_synced: 900,
    error_code: null,
    error_detail: null,
    retry_after_seconds: null,
    started_at: '2026-07-30T10:00:00Z',
    finished_at: '2026-07-30T10:01:00Z',
    duration_ms: 60000,
    is_running: false,
    result: 'success',
    ...over,
  });

  it('says Running while a sync is in flight', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': connected(),
        ...summary,
        ...syncState({
          running: true,
          run: run({ is_running: true, result: null, status: 'running' }),
          last_synced_at: null,
        }),
      }),
    );
    renderPage();

    expect(await screen.findByText('Running')).toBeDefined();
    expect(screen.getByText('Pulling orders from Shopify')).toBeDefined();
  });

  it('says Success after one lands', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': connected(),
        ...summary,
        ...syncState({ running: false, run: run({}), last_synced_at: '2026-07-30T10:01:00Z' }),
      }),
    );
    renderPage();

    expect(await screen.findByText('Success')).toBeDefined();
  });

  it('says Failed when one did not', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': connected(),
        ...summary,
        ...syncState({ running: false, run: run({ result: 'failed' }), last_synced_at: null }),
      }),
    );
    renderPage();

    expect(await screen.findByText('Failed')).toBeDefined();
  });

  it('says so plainly when nothing has ever run', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': connected(),
        ...summary,
        ...syncState({ running: false, run: null, last_synced_at: null }),
      }),
    );
    renderPage();

    expect(await screen.findByText('Not run yet')).toBeDefined();
  });
});

describe('sections the redesign removed', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': connected() }));
  });

  it('has no inventory comparison panel', async () => {
    renderPage();
    await screen.findByText('mystore.myshopify.com');

    expect(screen.queryByText('Inventory vs Shopify')).toBeNull();
  });

  it('has no KPI card strip above the store card', async () => {
    const { container } = renderPage();
    await screen.findByText('mystore.myshopify.com');

    expect(container.querySelector('.cardgrid')).toBeNull();
  });

  it('has no Sync now button', async () => {
    // A sync follows every import, so the page reports the store rather than
    // driving it.
    renderPage();
    await screen.findByText('mystore.myshopify.com');

    expect(screen.queryByRole('button', { name: /Sync now/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Syncing/i })).toBeNull();
  });
});

describe('recent syncs', () => {
  const run = (result: string) => ({
    id: 1,
    trigger: 'import',
    status: 'finished',
    stage: 'done',
    result,
    orders_synced: 400,
    line_items_synced: 900,
    orders_pct: 100,
    error_code: null,
    error_detail: null,
    retry_after_seconds: null,
    started_at: '2026-07-30T10:00:00Z',
    finished_at: '2026-07-30T10:01:00Z',
    duration_ms: 60000,
    is_running: false,
  });

  const history = (items: unknown[]) => ({
    'GET /shopify/syncs': {
      ok: true,
      status: 200,
      body: { items, total: items.length, limit: 4, offset: 0 },
    },
  });

  it('lists the timestamp and the result, and nothing else', async () => {
    vi.stubGlobal(
      'fetch',
      router({ 'GET /shopify/connection': connected(), ...history([run('success')]) }),
    );
    renderPage();

    const table = await screen.findByRole('table');
    const headers = Array.from(table.querySelectorAll('th')).map((th) => th.textContent);
    expect(headers).toEqual(['Date', 'Result']);
    expect(screen.queryByText('400')).toBeNull();
  });

  it('renders a coloured badge for failed', async () => {
    vi.stubGlobal(
      'fetch',
      router({ 'GET /shopify/connection': connected(), ...history([run('failed')]) }),
    );
    renderPage();

    expect(await screen.findByText('Failed')).toBeDefined();
  });

  it('shows a proper empty state, not a bare sentence', async () => {
    vi.stubGlobal('fetch', router({ 'GET /shopify/connection': connected(), ...history([]) }));
    renderPage();

    // The paragraph, not the heading: "No syncs yet" is the empty state's
    // heading and the panel arrives a tick after the status cell does, so
    // awaiting the heading could resolve on the wrong element.
    expect(
      await screen.findByText(
        'Import a sheet and Shopify sales will be pulled in automatically.',
      ),
    ).toBeDefined();
    expect(screen.getByText('No syncs yet')).toBeDefined();
  });
});

describe('the scope requirement', () => {
  it('never asks for read_products', async () => {
    /** The product does not call it, so demanding it sent users after a scope
        that could not help them. */
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/connection': {
          ok: true,
          status: 200,
          body: { connected: false, connection: null, source: 'none' },
        },
        'POST /shopify/connection/test': {
          ok: false,
          status: 400,
          body: {
            error: {
              code: 'shopify_missing_scopes',
              message: "That token doesn't have the access StockSync Analytics needs.",
              next: 'Grant read_orders to the app in its Configuration, then paste a new token.',
            },
          },
        },
      }),
    );
    const { container } = renderPage();

    await screen.findByLabelText(/Store URL/i);
    await userEvent.type(screen.getByLabelText(/Store URL/i), 'mystore.myshopify.com');
    await userEvent.type(screen.getByLabelText(/Admin API access token/i), 'example-token');
    await userEvent.click(screen.getByRole('button', { name: /Test connection/i }));

    expect(await screen.findByText(/Grant read_orders/)).toBeDefined();
    expect(container.textContent).not.toContain('read_products');
  });
});
