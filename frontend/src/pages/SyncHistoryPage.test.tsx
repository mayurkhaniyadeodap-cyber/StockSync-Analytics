// @vitest-environment jsdom
/** Sync history (design doc §9.1). */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SyncHistoryPage } from './SyncHistoryPage';
import { ShopifyStatusProvider } from '../contexts/ShopifyStatusContext';
import { ToastProvider } from '../contexts/ToastContext';
import type { SyncRun } from '../types/api';

function run(overrides: Partial<SyncRun> = {}): SyncRun {
  return {
    id: 1,
    trigger: 'manual',
    status: 'finished',
    stage: 'done',
    orders_pct: 100,
    orders_synced: 512,
    line_items_synced: 900,
    result: 'success',
    error_code: null,
    error_detail: null,
    retry_after_seconds: null,
    started_at: '2026-07-29T10:00:00Z',
    finished_at: '2026-07-29T10:00:12Z',
    duration_ms: 12000,
    is_running: false,
    ...overrides,
  };
}

type Route = { ok: boolean; status: number; body: unknown };

function router(routes: Record<string, Route>) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const key = Object.keys(routes).find((candidate) => {
      const [routeMethod = '', routePath = ''] = candidate.split(' ');
      return routeMethod === method && url.includes(routePath);
    });
    const route = (key ? routes[key] : undefined) ?? {
      ok: true,
      status: 200,
      body: { running: false, run: null, last_synced_at: null },
    };
    return Promise.resolve({
      ok: route.ok,
      status: route.status,
      json: () => Promise.resolve(route.body),
    } as Response);
  });
}

function history(items: SyncRun[]): Route {
  return { ok: true, status: 200, body: { items, total: items.length, limit: 50, offset: 0 } };
}

const IDLE: Route = {
  ok: true,
  status: 200,
  body: { running: false, run: null, last_synced_at: null },
};

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ShopifyStatusProvider>
          <SyncHistoryPage />
        </ShopifyStatusProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    router({ 'GET /shopify/syncs': history([]), 'GET /shopify/sync': IDLE }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SyncHistoryPage', () => {
  it('shows the empty state before any sync', async () => {
    renderPage();

    expect(await screen.findByText('No syncs yet')).toBeDefined();
  });

  it('renders a row per sync with its counts', async () => {
    vi.stubGlobal(
      'fetch',
      router({ 'GET /shopify/syncs': history([run()]), 'GET /shopify/sync': IDLE }),
    );
    renderPage();

    const table = await screen.findByRole('table');
    // Orders and line items — the two figures an orders-only sync produces.
    expect(within(table).getByText('512')).toBeDefined();
    expect(within(table).getByText('900')).toBeDefined();
    expect(within(table).getByText('Success')).toBeDefined();
    expect(within(table).getByText('12.0s')).toBeDefined();
  });

  it('describes a partial sync without calling it a failure', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/syncs': history([
          run({
            result: 'partial',
            error_detail: 'That token doesn’t have the access needed.',
          }),
        ]),
        'GET /shopify/sync': IDLE,
      }),
    );
    renderPage();

    const table = await screen.findByRole('table');
    expect(within(table).getByText('Partial')).toBeDefined();
    expect(within(table).getByText(/doesn’t have the access/)).toBeDefined();
  });

  it('shows a running sync as running rather than as a result', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/syncs': history([
          run({ result: null, is_running: true, duration_ms: null, finished_at: null }),
        ]),
        'GET /shopify/sync': IDLE,
      }),
    );
    renderPage();

    const table = await screen.findByRole('table');
    expect(within(table).getByText('Running')).toBeDefined();
  });

  it('requests the matching result when a filter is chosen', async () => {
    const fetchMock = router({ 'GET /shopify/syncs': history([]), 'GET /shopify/sync': IDLE });
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    await screen.findByText('No syncs yet');

    await userEvent.click(screen.getByRole('button', { name: 'Failed' }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('result=failed'))).toBe(true);
    });
  });

  it('offers no way to start a sync from here', async () => {
    /**
     * Syncs run after every import now, so this page reports history rather
     * than making it. The one manual control left is on the Shopify page,
     * which is also where a failed sync is retried from.
     */
    vi.stubGlobal(
      'fetch',
      router({ 'GET /shopify/syncs': history([]), 'GET /shopify/sync': IDLE }),
    );
    renderPage();
    await screen.findByText('No syncs yet');

    expect(screen.queryByRole('button', { name: /Sync now/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Syncing/i })).toBeNull();
  });

  it('still shows a run that is in flight', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/syncs': history([run({ is_running: true, result: null })]),
        'GET /shopify/sync': {
          ok: true,
          status: 200,
          body: {
            running: true,
            run: run({ is_running: true, result: null }),
            last_synced_at: null,
          },
        },
      }),
    );
    renderPage();

    expect(await screen.findByText(/Running/i)).toBeDefined();
  });

  it('surfaces a load failure with a retry', async () => {
    vi.stubGlobal(
      'fetch',
      router({
        'GET /shopify/syncs': {
          ok: false,
          status: 500,
          body: {
            error: { code: 'internal_error', message: 'Something went wrong.', next: 'Retry.' },
          },
        },
        'GET /shopify/sync': IDLE,
      }),
    );
    renderPage();

    expect(await screen.findByText('Something went wrong.')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDefined();
  });
});
