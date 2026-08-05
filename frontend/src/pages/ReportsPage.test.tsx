// @vitest-environment jsdom
/** Reports — design doc §12. */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReportsPage } from './ReportsPage';
import { ToastProvider } from '../contexts/ToastContext';
import type { Report, ReportPreview } from '../types/api';

const PREVIEW: ReportPreview = {
  title: 'Inventory report',
  subtitle: '125 SKUs from the imported sheet',
  columns: [
    { header: 'SKU', align: 'left' },
    { header: 'Product name', align: 'left' },
    { header: 'Current inventory', align: 'right' },
  ],
  rows: [
    ['DD-1001', 'Steel Bottle 750ml', '204'],
    ['DD-1002', 'Blue Hoodie', '4'],
  ],
  truncated: true,
};

function report(overrides: Partial<Report> = {}): Report {
  return {
    id: 1,
    kind: 'inventory',
    fmt: 'csv',
    status: 'ready',
    filename: 'stocksync-inventory-20260729-153000.csv',
    range_days: 30,
    range_label: 'Last 30 days',
    row_count: 125,
    size_bytes: 8192,
    error_code: null,
    error_detail: null,
    created_at: '2026-07-29T15:30:00Z',
    completed_at: '2026-07-29T15:30:01Z',
    ...overrides,
  };
}

type Route = { ok: boolean; status: number; body: unknown };

function routes(overrides: Record<string, Route> = {}) {
  const defaults: Record<string, Route> = {
    'GET /reports/preview': { ok: true, status: 200, body: PREVIEW },
    'GET /reports': {
      ok: true,
      status: 200,
      body: { items: [], total: 0, limit: 20, offset: 0 },
    },
    'POST /reports': { ok: true, status: 202, body: report() },
    'DELETE /reports': { ok: true, status: 204, body: null },
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
    <ToastProvider>
      <ReportsPage />
    </ToastProvider>,
  );
}

const assign = vi.fn();

beforeEach(() => {
  // window.location.assign is not implemented in jsdom.
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, assign },
  });
});

afterEach(() => {
  cleanup();
  assign.mockReset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('preview', () => {
  it('shows what the export will contain', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    expect(await screen.findByText('Inventory report')).toBeDefined();
    expect(screen.getByText('125 SKUs from the imported sheet')).toBeDefined();
    expect(screen.getByText('DD-1001')).toBeDefined();
    expect(screen.getByText('Showing the first 2 rows of the export.')).toBeDefined();
  });

  it('right-aligns the columns the server marked as figures', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    const header = await screen.findByRole('columnheader', { name: 'Current inventory' });
    expect(header.className).toContain('n');
    expect(screen.getByRole('columnheader', { name: 'SKU' }).className).not.toContain('n');
  });

  it('refetches when the report type changes', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await screen.findByText('DD-1001');
    fetch.mockClear();
    await userEvent.click(screen.getByRole('button', { name: 'Sales' }));

    await waitFor(() => {
      const asked = fetch.mock.calls.map((call) => String(call[0]));
      expect(asked.some((url) => url.includes('kind=sales'))).toBe(true);
    });
  });

  it('refetches when the range changes', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await screen.findByText('DD-1001');
    fetch.mockClear();
    await userEvent.selectOptions(screen.getByLabelText('Range'), 'fy');

    await waitFor(() => {
      const asked = fetch.mock.calls.map((call) => String(call[0]));
      expect(asked.some((url) => url.includes('range_option=fy'))).toBe(true);
    });
  });

  it('shows the §12.3 empty state when there is nothing to report on', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /reports/preview': {
          ok: true,
          status: 200,
          body: { ...PREVIEW, rows: [], truncated: false },
        },
      }),
    );
    renderPage();

    expect(
      await screen.findByText('No data available to generate a report yet.'),
    ).toBeDefined();
  });

  it('shows the server’s message and a retry when the preview fails', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /reports/preview': {
          ok: false,
          status: 500,
          body: {
            error: {
              code: 'preview_failed',
              message: 'The preview could not be built.',
              next: 'Try again.',
            },
          },
        },
      }),
    );
    renderPage();

    expect(await screen.findByText('The preview could not be built.')).toBeDefined();
    expect(screen.getByRole('button', { name: /Retry/ })).toBeDefined();
  });
});

describe('exporting', () => {
  it('sends the chosen type, format and range', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await screen.findByText('DD-1001');
    await userEvent.click(screen.getByRole('button', { name: 'SKU performance' }));
    await userEvent.click(screen.getByRole('button', { name: 'Excel' }));
    await userEvent.click(screen.getByRole('button', { name: /Export Excel/ }));

    await waitFor(() => {
      const posted = fetch.mock.calls.find(
        (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(posted).toBeDefined();
      expect(JSON.parse(String((posted?.[1] as RequestInit).body))).toEqual({
        kind: 'sku_performance',
        fmt: 'xlsx',
        range_option: '30',
        // Off unless asked for: an export carries every analysed SKU.
        top_only: false,
      });
    });
  });

  it('the export button follows the chosen format', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('DD-1001');
    expect(screen.getByRole('button', { name: /Export CSV/ })).toBeDefined();

    await userEvent.click(screen.getByRole('button', { name: 'PDF' }));

    expect(screen.getByRole('button', { name: /Export PDF/ })).toBeDefined();
  });

  it('disables the button and says so while generating (§12.3)', async () => {
    let release: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    const fetch = routes();
    const slow = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'POST') {
        return pending.then(() => fetch(input, init));
      }
      return fetch(input, init);
    });
    vi.stubGlobal('fetch', slow);
    renderPage();

    await screen.findByText('DD-1001');
    await userEvent.click(screen.getByRole('button', { name: /Export CSV/ }));

    const button = await screen.findByRole('button', { name: /Generating report/ });
    expect(button.hasAttribute('disabled')).toBe(true);

    release(null);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Export CSV/ })).toBeDefined();
    });
  });

  it('reports the failure inline rather than silently doing nothing', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /reports': {
          ok: false,
          status: 500,
          body: {
            error: {
              code: 'report_failed',
              message: 'Export failed — try again.',
              next: 'Try again.',
            },
          },
        },
      }),
    );
    renderPage();

    await screen.findByText('DD-1001');
    await userEvent.click(screen.getByRole('button', { name: /Export CSV/ }));

    expect(await screen.findByText('Export failed — try again.')).toBeDefined();
  });
});

describe('export centre', () => {
  const withHistory = (items: Report[]) => ({
    'GET /reports': {
      ok: true,
      status: 200,
      body: { items, total: items.length, limit: 20, offset: 0 },
    },
  });

  it('lists a ready report with its size and row count', async () => {
    vi.stubGlobal('fetch', routes(withHistory([report()])));
    renderPage();

    expect(await screen.findByText('Export centre')).toBeDefined();
    expect(screen.getByText('stocksync-inventory-20260729-153000.csv')).toBeDefined();
    expect(screen.getByText('Last 30 days · 125 rows · 8 KB')).toBeDefined();
    expect(screen.getByText('Ready')).toBeDefined();
  });

  it('shows Preparing without a download button', async () => {
    vi.stubGlobal('fetch', routes(withHistory([report({ status: 'preparing' })])));
    renderPage();

    expect(await screen.findByText('Preparing')).toBeDefined();
    expect(screen.queryByRole('button', { name: /Download/ })).toBeNull();
  });

  it('shows Failed with the reason the server gave', async () => {
    vi.stubGlobal(
      'fetch',
      routes(
        withHistory([
          report({
            status: 'failed',
            error_code: 'report_interrupted',
            error_detail: 'The server restarted while this report was being prepared.',
          }),
        ]),
      ),
    );
    renderPage();

    expect(await screen.findByText('Failed')).toBeDefined();
    expect(
      screen.getByText('The server restarted while this report was being prepared.'),
    ).toBeDefined();
  });

  it('downloads through a navigation so the cookie and filename come along', async () => {
    vi.stubGlobal('fetch', routes(withHistory([report()])));
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /Download/ }));

    expect(assign).toHaveBeenCalledWith('/api/reports/1/download');
  });

  it('deletes a report and drops it from the list', async () => {
    const fetch = routes(withHistory([report()]));
    vi.stubGlobal('fetch', fetch);
    renderPage();

    const row = await screen.findByText('stocksync-inventory-20260729-153000.csv');
    await userEvent.click(screen.getByRole('button', { name: /Delete stocksync-inventory/ }));

    await waitFor(() => {
      const deleted = fetch.mock.calls.find(
        (call) => (call[1] as RequestInit | undefined)?.method === 'DELETE',
      );
      expect(String(deleted?.[0])).toContain('/reports/1');
    });
    expect(row.isConnected).toBe(false);
  });

  it('stays hidden until something has been exported', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('DD-1001');

    expect(screen.queryByText('Export centre')).toBeNull();
  });

  it('polls only while a report is preparing', async () => {
    vi.useFakeTimers();
    try {
      const fetch = routes(withHistory([report({ status: 'ready' })]));
      vi.stubGlobal('fetch', fetch);
      renderPage();

      await vi.waitFor(() => {
        expect(screen.getByText('Ready')).toBeDefined();
      });
      fetch.mockClear();
      await vi.advanceTimersByTimeAsync(5000);

      expect(fetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('accessibility', () => {
  it('the type and format controls announce what is selected', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('DD-1001');
    const types = screen.getByRole('group', { name: 'Report type' });

    expect(
      within(types).getByRole('button', { name: 'Inventory' }).getAttribute('aria-pressed'),
    ).toBe('true');
    expect(screen.getByRole('button', { name: 'CSV' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
  });
});

describe('completion notification', () => {
  /**
   * The bug this guards: the toast keyed off the POST response's status, but
   * with the threaded worker that is always "preparing" — so the toast only
   * ever fired under the inline test runner, never in production.
   */
  function sequence(...pages: Report[][]) {
    let call = 0;
    const body = () => {
      const items = pages[Math.min(call++, pages.length - 1)] ?? [];
      return { items, total: items.length, limit: 20, offset: 0 };
    };
    return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      let payload: unknown = {};
      if (url.includes('/reports/preview')) payload = PREVIEW;
      else if (method === 'POST') payload = report({ status: 'preparing' });
      else if (url.includes('/reports')) payload = body();
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(payload),
      } as Response);
    });
  }

  it('toasts when polling sees preparing become ready', async () => {
    vi.stubGlobal(
      'fetch',
      sequence([report({ status: 'preparing' })], [report({ status: 'ready' })]),
    );
    renderPage();

    await screen.findByText('DD-1001');
    await userEvent.click(screen.getByRole('button', { name: /Export CSV/ }));

    const toasts = await screen.findByRole('status');
    await waitFor(
      () => {
        expect(
          within(toasts).getByText('Report ready — stocksync-inventory-20260729-153000.csv'),
        ).toBeDefined();
      },
      { timeout: 5000 },
    );
  });

  it('toasts the reason when polling sees preparing become failed', async () => {
    vi.stubGlobal(
      'fetch',
      sequence(
        [report({ status: 'preparing' })],
        [
          report({
            status: 'failed',
            error_code: 'report_failed',
            error_detail: 'RuntimeError while building the file.',
          }),
        ],
      ),
    );
    renderPage();

    await screen.findByText('DD-1001');
    await userEvent.click(screen.getByRole('button', { name: /Export CSV/ }));

    // Scoped to the toast region: the same sentence also renders in the row's
    // description, so an unscoped query matches twice and never resolves.
    const toasts = await screen.findByRole('status');
    await waitFor(
      () => {
        expect(within(toasts).getByText('RuntimeError while building the file.')).toBeDefined();
      },
      { timeout: 5000 },
    );
  });

  it('does not toast for a report that was already ready when the page loaded', async () => {
    vi.stubGlobal('fetch', sequence([report({ status: 'ready' })]));
    renderPage();

    await screen.findByText('DD-1001');
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(screen.queryByText(/Report ready —/)).toBeNull();
  });
});

describe('Export Top 50', () => {
  it('is off unless it is asked for', async () => {
    /** An export is the copy people file and re-read: it carries everything. */
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();

    await screen.findByText('DD-1001');
    const box = screen.getByRole('checkbox', { name: /Export Top 50 only/ });
    expect(box).toHaveProperty('checked', false);
  });

  it('sends the choice when it is ticked', async () => {
    const fetcher = routes();
    vi.stubGlobal('fetch', fetcher);
    renderPage();
    await screen.findByText('DD-1001');

    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: /Export Top 50 only/ }));
    await user.click(screen.getByRole('button', { name: /Export CSV/ }));

    await waitFor(() => {
      const posted = fetcher.mock.calls.find(
        ([input, init]) =>
          (init as RequestInit | undefined)?.method === 'POST' &&
          String(input).includes('/reports'),
      );
      expect(posted).toBeDefined();
      expect(
        (JSON.parse(String((posted?.[1] as RequestInit).body)) as Record<string, unknown>)
          .top_only,
      ).toBe(true);
    });
  });
});
