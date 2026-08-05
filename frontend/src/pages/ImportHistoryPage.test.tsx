// @vitest-environment jsdom
/**
 * Import History (design doc §8.8).
 *
 * jsdom is selected per file rather than globally: the rest of the suite is
 * pure logic and runs faster in node, and vitest.config.ts stays untouched.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ImportHistoryPage } from './ImportHistoryPage';
import type { ImportBatchSummary } from '../types/api';

function batch(overrides: Partial<ImportBatchSummary> = {}): ImportBatchSummary {
  return {
    id: 1,
    method: 'csv_upload',
    origin_filename: 'july.csv',
    status: 'complete',
    rows_read: 10,
    rows_imported: 10,
    rows_merged: 0,
    rows_flagged: 0,
    rows_rejected: 0,
    error_code: null,
    error_detail: null,
    started_at: '2026-07-28T10:00:00Z',
    finished_at: '2026-07-28T10:00:01Z',
    duration_ms: 1000,
    ...overrides,
  };
}

function respondWith(items: ImportBatchSummary[]) {
  // Parameters declared so the mock's recorded calls stay typed — see
  // noUncheckedIndexedAccess in tsconfig.
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ items, total: items.length, limit: 50, offset: 0 }),
    } as Response),
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ImportHistoryPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal('fetch', respondWith([]));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ImportHistoryPage', () => {
  it('shows the empty state when nothing has been imported', async () => {
    renderPage();

    expect(await screen.findByText(/haven’t imported any inventory yet/i)).toBeDefined();
  });

  // csv_url is absent: URL import was removed from the product, and no batch in
  // any database carries it. An unknown method still renders — methodLabel falls
  // back to the raw value — so History cannot show a blank cell either way.
  it.each([
    ['google_sheet', 'Google Sheet'],
    ['csv_upload', 'CSV upload'],
    ['excel_upload', 'Excel upload'],
  ])('names a %s import as "%s"', async (method, label) => {
    // The one thing History shows that the import itself does not: how it arrived.
    vi.stubGlobal('fetch', respondWith([batch({ method })]));
    renderPage();

    expect(await screen.findByText(label)).toBeDefined();
  });

  it('renders a row per import with its counts', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith([batch({ origin_filename: 'stock.csv', rows_imported: 1238 })]),
    );

    renderPage();

    expect(await screen.findByText('stock.csv')).toBeDefined();
    // Indian digit grouping, via lib/format.
    expect(screen.getByText('1,238')).toBeDefined();
    // Scoped to the table: "Complete" is also the name of a filter button.
    expect(within(screen.getByRole('table')).getByText('Complete')).toBeDefined();
  });

  it('shows the error message on a failed import', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith([
        batch({
          status: 'failed',
          rows_imported: 0,
          error_code: 'missing_headers',
          error_detail: 'That sheet is missing a column StockSync Analytics needs.',
        }),
      ]),
    );

    renderPage();

    expect(await screen.findByText('Failed')).toBeDefined();
    expect(screen.getByText(/missing a column/i)).toBeDefined();
  });

  it('describes a partial import rather than calling it a failure', async () => {
    vi.stubGlobal(
      'fetch',
      respondWith([batch({ status: 'partial', rows_rejected: 2, rows_flagged: 2 })]),
    );

    renderPage();

    expect(await screen.findByText('Partial')).toBeDefined();
    expect(screen.getByText(/2 rows rejected/i)).toBeDefined();
  });

  it('reports merged duplicates', async () => {
    vi.stubGlobal('fetch', respondWith([batch({ rows_merged: 3, rows_flagged: 3 })]));

    renderPage();

    expect(await screen.findByText(/3 duplicates merged/i)).toBeDefined();
  });

  it('requests the matching status when a filter is chosen', async () => {
    const fetchMock = respondWith([]);
    vi.stubGlobal('fetch', fetchMock);
    renderPage();
    await screen.findByText(/haven’t imported/i);

    await userEvent.click(screen.getByRole('button', { name: 'Failed' }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('status=failed'))).toBe(true);
    });
  });

  it('surfaces a load failure with a retry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 500,
          json: () =>
            Promise.resolve({
              error: {
                code: 'internal_error',
                message: 'Something went wrong.',
                next: 'Retry.',
              },
            }),
        } as Response),
      ),
    );

    renderPage();

    expect(await screen.findByText('Something went wrong.')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeDefined();
  });
});
