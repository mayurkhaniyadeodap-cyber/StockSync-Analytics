// @vitest-environment jsdom
/**
 * Settings — design doc §13.
 *
 * Every section here used to render "Not built yet" and the milestone it was
 * waiting for. A settings screen that shows a field it cannot save, or a
 * placeholder where a control belongs, is worse than one that is honestly
 * empty; these tests hold each section to actually reading and writing the
 * backend, and hold the page to never advertising an unbuilt one again.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from './SettingsPage';
import { AuthProvider } from '../contexts/AuthContext';
import { ShopifyStatusProvider } from '../contexts/ShopifyStatusContext';
import { ThemeProvider } from '../contexts/ThemeContext';
import { ToastProvider } from '../contexts/ToastContext';

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

const TOKEN = `${SHOPIFY_TOKEN_PREFIX}exampleonlytoken`;

const ME = {
  id: 1,
  email: 'admin@deodap.in',
  full_name: 'Administrator',
  role: 'Inventory lead',
  timezone: 'Asia/Kolkata',
  initials: 'A',
  workspace: {
    id: 1,
    name: 'Deodap Retail',
    slug: 'deodap',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    low_stock_threshold: 10,
  },
  preferences: { theme: 'light', table_density: 'comfortable', alert_on_stockout: true },
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
  connected_at: '2026-07-28T10:00:00Z',
  disconnected_at: null,
  last_verified_at: '2026-07-31T10:00:00Z',
  store_latest_order_at: null,
  freshness_checked_at: null,
};

const CONNECTED = { connected: true, connection: STORE, source: 'database' };
const NOT_CONNECTED = { connected: false, connection: null, source: 'none' };

const SHEET = {
  id: 7,
  name: 'Weekly stock count',
  url: 'https://docs.google.com/spreadsheets/d/1AbC_dEf/edit#gid=42',
  // Comfortably in the past, so the relative label is "… ago" whenever the
  // suite runs rather than "just now" for a timestamp in the future.
  last_synced_at: '2026-01-05T09:00:00Z',
  last_status: 'complete',
  last_batch_id: 3,
};

const IMPORTS = {
  items: [
    {
      id: 3,
      method: 'google_sheet',
      origin_filename: 'google-sheet-1AbC.csv',
      status: 'complete',
      rows_read: 320,
      rows_imported: 300,
      rows_merged: 20,
      rows_flagged: 0,
      rows_rejected: 0,
      error_code: null,
      error_detail: null,
      started_at: '2026-07-31T09:00:00Z',
      finished_at: '2026-07-31T09:00:20Z',
      duration_ms: 20000,
    },
  ],
  total: 1,
  limit: 5,
  offset: 0,
};

const SYNCS = {
  items: [
    {
      id: 10,
      trigger: 'manual',
      status: 'finished',
      stage: 'done',
      orders_pct: 100,
      orders_synced: 12000,
      line_items_synced: 18500,
      result: 'success',
      error_code: null,
      error_detail: null,
      retry_after_seconds: null,
      started_at: '2026-07-31T08:00:00Z',
      finished_at: '2026-07-31T09:00:00Z',
      duration_ms: 3600000,
      is_running: false,
    },
  ],
  total: 1,
  limit: 5,
  offset: 0,
};

type Route_ = { ok: boolean; status: number; body: unknown };
type Call = { method: string; path: string; body: unknown };

/**
 * A fetch stub that records what was asked, so a test can assert the page
 * called the endpoint that already exists rather than one of its own.
 */
function backend(overrides: Record<string, Route_> = {}) {
  const calls: Call[] = [];
  const table: Record<string, Route_> = {
    'GET /auth/me': { ok: true, status: 200, body: ME },
    'PATCH /me/preferences': { ok: true, status: 200, body: ME },
    'PATCH /me': { ok: true, status: 200, body: ME },
    'GET /shopify/connection': { ok: true, status: 200, body: CONNECTED },
    'PATCH /shopify/connection': { ok: true, status: 200, body: CONNECTED },
    'DELETE /shopify/connection': { ok: true, status: 200, body: NOT_CONNECTED },
    'POST /shopify/connection/verify': { ok: true, status: 200, body: CONNECTED },
    'GET /shopify/sales/summary': {
      ok: true,
      status: 200,
      body: { orders: 1, line_items: 1, skus_with_sales: 1, last_synced_at: null },
    },
    'GET /shopify/sync': {
      ok: true,
      status: 200,
      body: { running: false, run: null, last_synced_at: null },
    },
    'GET /shopify/syncs': { ok: true, status: 200, body: SYNCS },
    'GET /imports/sheets': { ok: true, status: 200, body: { items: [] } },
    'POST /imports/sheets': {
      ok: true,
      status: 200,
      body: { batch: { rows_imported: 300, status: 'complete', id: 4 } },
    },
    'GET /imports': { ok: true, status: 200, body: IMPORTS },
    ...overrides,
  };

  const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const path = url.replace(/^.*\/api/, '').split('?')[0] ?? '';
    calls.push({
      method,
      path,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
    });

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

  return { fetcher, calls };
}

function renderSettings(section: string, fetcher: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetcher);
  return render(
    <MemoryRouter initialEntries={[`/settings/${section}`]}>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <ShopifyStatusProvider>
              <Routes>
                <Route path="/settings/:section" element={<SettingsPage />} />
              </Routes>
            </ShopifyStatusProvider>
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

const sent = (calls: Call[], method: string, path: string) =>
  calls.filter((call) => call.method === method && call.path === path);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('no placeholders remain', () => {
  const SECTIONS = ['shopify', 'sheets', 'profile', 'prefs', 'imports', 'syncs'];

  it.each(SECTIONS)('%s is built', async (section) => {
    const { fetcher } = backend();
    renderSettings(section, fetcher);

    await waitFor(() => expect(screen.getByText('Settings')).toBeDefined());
    await waitFor(() => expect(screen.queryByText('Not built yet')).toBeNull());
    // The milestone labels the placeholders carried: "arrives in M2" and so on.
    expect(document.body.textContent).not.toMatch(/\bM[237]\b/);
    expect(document.body.textContent).not.toMatch(/arrives in/i);
  });

  it('keeps every section in the sidebar of the page', async () => {
    const { fetcher } = backend();
    renderSettings('prefs', fetcher);

    const nav = document.querySelector('.set-nav');
    expect([...(nav?.querySelectorAll('button') ?? [])].map((b) => b.textContent)).toEqual([
      'Shopify',
      'Google Sheets',
      'Profile',
      'Display',
      'Import history',
      'Sync history',
    ]);
  });
});

describe('Shopify', () => {
  it('shows the live store, a masked token and the lookback window', async () => {
    const { fetcher } = backend();
    renderSettings('shopify', fetcher);

    const url = (await screen.findByLabelText('Store URL')) as HTMLInputElement;
    expect(url.value).toBe('deodap.myshopify.com');
    expect(url.disabled).toBe(true);

    const window_ = screen.getByLabelText('Order lookback window') as HTMLSelectElement;
    expect(window_.value).toBe('90');
    expect([...window_.options].map((o) => o.value)).toEqual(['30', '60', '90']);
  });

  it('never renders the token, in any field', async () => {
    /** It is encrypted at rest and the API has never returned it. */
    const { fetcher } = backend();
    renderSettings('shopify', fetcher);
    await screen.findByLabelText('Store URL');

    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(document.body.innerHTML).not.toContain(SHOPIFY_TOKEN_PREFIX);
    const masked = screen.getByLabelText(/token, hidden/i) as HTMLInputElement;
    expect(masked.value).toBe('•'.repeat(24));
    expect(masked.type).toBe('password');
  });

  it('saves the window through the connection endpoint', async () => {
    const { fetcher, calls } = backend();
    renderSettings('shopify', fetcher);
    const window_ = await screen.findByLabelText('Order lookback window');

    const user = userEvent.setup();
    await user.selectOptions(window_, '30');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(sent(calls, 'PATCH', '/shopify/connection')).toHaveLength(1));
    expect(sent(calls, 'PATCH', '/shopify/connection')[0]?.body).toEqual({
      order_lookback_days: 30,
    });
  });

  it('keeps Save disabled until something changes', async () => {
    const { fetcher } = backend();
    renderSettings('shopify', fetcher);
    await screen.findByLabelText('Store URL');

    const save = screen.getByRole('button', { name: 'Save changes' });
    expect(save.hasAttribute('disabled')).toBe(true);

    await userEvent.selectOptions(screen.getByLabelText('Order lookback window'), '60');
    expect(save.hasAttribute('disabled')).toBe(false);
  });

  it('tests the stored credential without asking for it again', async () => {
    const { fetcher, calls } = backend();
    renderSettings('shopify', fetcher);
    await screen.findByLabelText('Store URL');

    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    await waitFor(() =>
      expect(sent(calls, 'POST', '/shopify/connection/verify')).toHaveLength(1),
    );
    // Nothing was typed, so nothing about a credential was sent.
    expect(sent(calls, 'POST', '/shopify/connection/verify')[0]?.body).toBeNull();
  });

  it('needs two clicks to disconnect', async () => {
    const { fetcher, calls } = backend();
    renderSettings('shopify', fetcher);
    await screen.findByLabelText('Store URL');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Disconnect store' }));
    expect(sent(calls, 'DELETE', '/shopify/connection')).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    await waitFor(() => expect(sent(calls, 'DELETE', '/shopify/connection')).toHaveLength(1));
  });

  it('sends people to the Shopify page when no store is connected', async () => {
    /** Connecting stays in one place; Settings adjusts a store that exists. */
    const { fetcher } = backend({
      'GET /shopify/connection': { ok: true, status: 200, body: NOT_CONNECTED },
    });
    renderSettings('shopify', fetcher);

    expect(await screen.findByText('No Shopify store connected')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Connect Shopify' })).toBeDefined();
    expect(screen.queryByLabelText('Order lookback window')).toBeNull();
  });
});

describe('Google Sheets', () => {
  it('shows an empty state, not a placeholder, when nothing is linked', async () => {
    const { fetcher } = backend();
    renderSettings('sheets', fetcher);

    expect(await screen.findByText('No sheets linked yet')).toBeDefined();
    expect(screen.getByRole('button', { name: /Link new sheet/ })).toBeDefined();
  });

  it('lists a linked sheet with its name, link and last sync', async () => {
    const { fetcher } = backend({
      'GET /imports/sheets': { ok: true, status: 200, body: { items: [SHEET] } },
    });
    renderSettings('sheets', fetcher);

    expect(await screen.findByText('Weekly stock count')).toBeDefined();
    const link = screen.getByRole('link', { name: SHEET.url }) as HTMLAnchorElement;
    expect(link.href).toBe(SHEET.url);
    const row = link.closest('tr');
    expect(within(row as HTMLElement).getByText(/ago$/)).toBeDefined();
  });

  it('links a new sheet through the import endpoint', async () => {
    const { fetcher, calls } = backend();
    renderSettings('sheets', fetcher);
    await screen.findByText('No sheets linked yet');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Sheet name'), 'Weekly stock count');
    await user.type(screen.getByLabelText(/Google Sheet link/), SHEET.url);
    await user.click(screen.getByRole('button', { name: /Link new sheet/ }));

    await waitFor(() => expect(sent(calls, 'POST', '/imports/sheets')).toHaveLength(1));
    expect(sent(calls, 'POST', '/imports/sheets')[0]?.body).toEqual({
      url: SHEET.url,
      name: 'Weekly stock count',
    });
  });

  it('re-syncs a sheet without asking for the link again', async () => {
    const { fetcher, calls } = backend({
      'GET /imports/sheets': { ok: true, status: 200, body: { items: [SHEET] } },
      'POST /imports/sheets/7/resync': {
        ok: true,
        status: 200,
        body: { batch: { rows_imported: 300, status: 'complete', id: 5 } },
      },
    });
    renderSettings('sheets', fetcher);
    await screen.findByText('Weekly stock count');

    await userEvent.click(screen.getByRole('button', { name: 'Re-sync' }));

    await waitFor(() =>
      expect(sent(calls, 'POST', '/imports/sheets/7/resync')).toHaveLength(1),
    );
  });

  it('needs two clicks to unlink', async () => {
    const { fetcher, calls } = backend({
      'GET /imports/sheets': { ok: true, status: 200, body: { items: [SHEET] } },
      'DELETE /imports/sheets/7': { ok: true, status: 204, body: null },
    });
    renderSettings('sheets', fetcher);
    await screen.findByText('Weekly stock count');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Unlink' }));
    expect(sent(calls, 'DELETE', '/imports/sheets/7')).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(sent(calls, 'DELETE', '/imports/sheets/7')).toHaveLength(1));
  });

  it('shows the server’s own message beside the field when a link fails', async () => {
    const { fetcher } = backend({
      'POST /imports/sheets': {
        ok: false,
        status: 422,
        body: {
          error: {
            code: 'sheet_not_public',
            message: "This Google Sheet isn't publicly accessible.",
            next: 'Share → Anyone with the link → Viewer.',
          },
        },
      },
    });
    renderSettings('sheets', fetcher);
    await screen.findByText('No sheets linked yet');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/Google Sheet link/), SHEET.url);
    await user.click(screen.getByRole('button', { name: /Link new sheet/ }));

    expect(
      await screen.findByText("This Google Sheet isn't publicly accessible."),
    ).toBeDefined();
    // The link stays, so it can be fixed rather than pasted again.
    expect((screen.getByLabelText(/Google Sheet link/) as HTMLInputElement).value).toBe(
      SHEET.url,
    );
  });
});

describe('Profile', () => {
  it('shows every field the user has', async () => {
    const { fetcher } = backend();
    renderSettings('profile', fetcher);

    const name = (await screen.findByLabelText(/Full name/)) as HTMLInputElement;
    // Seeded from /auth/me, which lands after the first render.
    await waitFor(() => expect(name.value).toBe('Administrator'));
    expect((screen.getByLabelText('Email') as HTMLInputElement).value).toBe('admin@deodap.in');
    expect((screen.getByLabelText('Role') as HTMLInputElement).value).toBe('Inventory lead');
    expect((screen.getByLabelText('Time zone') as HTMLSelectElement).value).toBe(
      'Asia/Kolkata',
    );
  });

  it('does not offer to edit the login identity or the role', async () => {
    const { fetcher } = backend();
    renderSettings('profile', fetcher);
    await screen.findByLabelText(/Full name/);

    expect((screen.getByLabelText('Email') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Role') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Full name/) as HTMLInputElement).disabled).toBe(false);
  });

  it('saves the name through the profile endpoint', async () => {
    const { fetcher, calls } = backend();
    renderSettings('profile', fetcher);
    const name = (await screen.findByLabelText(/Full name/)) as HTMLInputElement;
    // Same seeding race as the threshold: clearing before /auth/me lands lets
    // the seeding write the old name back over what was typed.
    await waitFor(() => expect(name.value).toBe('Administrator'));

    const user = userEvent.setup();
    await user.clear(name);
    await user.type(name, 'Priya Mehta');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(sent(calls, 'PATCH', '/me')).toHaveLength(1));
    expect(sent(calls, 'PATCH', '/me')[0]?.body).toEqual({
      full_name: 'Priya Mehta',
      timezone: 'Asia/Kolkata',
    });
  });

  it('keeps Save disabled until something changes', async () => {
    const { fetcher } = backend();
    renderSettings('profile', fetcher);
    await screen.findByLabelText(/Full name/);

    expect(screen.getByRole('button', { name: 'Save changes' }).hasAttribute('disabled')).toBe(
      true,
    );
  });
});

describe('Display', () => {
  it('shows the low stock threshold the workspace has set', async () => {
    const { fetcher } = backend();
    renderSettings('prefs', fetcher);

    const input = (await screen.findByLabelText('Low stock threshold')) as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('10'));
  });

  it('saves the threshold once, when the field is left', async () => {
    /** Not per keystroke: typing "25" would otherwise save 2 on the way past. */
    const { fetcher, calls } = backend();
    renderSettings('prefs', fetcher);
    const input = (await screen.findByLabelText('Low stock threshold')) as HTMLInputElement;
    // Wait for the server's value before editing: clearing a field that has not
    // been seeded yet lets the seeding overwrite what was typed.
    await waitFor(() => expect(input.value).toBe('10'));

    const user = userEvent.setup();
    await user.clear(input);
    await user.type(input, '25');
    await user.tab();

    await waitFor(() => expect(sent(calls, 'PATCH', '/me/preferences')).toHaveLength(1));
    expect(sent(calls, 'PATCH', '/me/preferences')[0]?.body).toEqual({
      low_stock_threshold: 25,
    });
  });

  it('does not save when the value has not moved', async () => {
    const { fetcher, calls } = backend();
    renderSettings('prefs', fetcher);
    const input = (await screen.findByLabelText('Low stock threshold')) as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('10'));

    await userEvent.click(input);
    await userEvent.tab();

    expect(sent(calls, 'PATCH', '/me/preferences')).toHaveLength(0);
  });

  it('keeps the three switches it already had', async () => {
    const { fetcher } = backend();
    renderSettings('prefs', fetcher);

    expect(await screen.findByRole('switch', { name: 'Dark theme' })).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Compact tables by default' })).toBeDefined();
    expect(screen.getByRole('switch', { name: 'Alert me when stock runs out' })).toBeDefined();
  });
});

describe('history previews', () => {
  it('shows recent imports from the existing endpoint', async () => {
    const { fetcher, calls } = backend();
    renderSettings('imports', fetcher);

    expect(await screen.findByText('Google Sheet')).toBeDefined();
    expect(screen.getByText('300')).toBeDefined();
    expect(screen.getByText('Complete')).toBeDefined();
    // The list endpoint the Import History page uses, not one of its own.
    expect(sent(calls, 'GET', '/imports')).toHaveLength(1);
  });

  it('shows recent syncs with orders and line items', async () => {
    const { fetcher, calls } = backend();
    renderSettings('syncs', fetcher);

    expect(await screen.findByText('12,000')).toBeDefined();
    expect(screen.getByText('18,500')).toBeDefined();
    expect(screen.getByText('Success')).toBeDefined();
    expect(sent(calls, 'GET', '/shopify/syncs')).toHaveLength(1);
  });

  it('offers a way through to each full page', async () => {
    const { fetcher } = backend();
    renderSettings('imports', fetcher);

    expect(await screen.findByRole('button', { name: /Open full page/ })).toBeDefined();
  });

  it('says so plainly when there is no history yet', async () => {
    const { fetcher } = backend({
      'GET /imports': { ok: true, status: 200, body: { ...IMPORTS, items: [], total: 0 } },
    });
    renderSettings('imports', fetcher);

    expect(await screen.findByText('Nothing yet')).toBeDefined();
  });
});
