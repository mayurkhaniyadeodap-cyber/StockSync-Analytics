// @vitest-environment jsdom
/** Inventory import — design doc §8.1–§8.7. */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ImportPage } from './ImportPage';
import { ToastProvider } from '../contexts/ToastContext';

type Route = { ok: boolean; status: number; body: unknown };

const IMPORTED: Route = {
  ok: true,
  status: 200,
  body: {
    batch: {
      id: 1,
      method: 'csv_upload',
      origin_filename: 'stock.csv',
      status: 'complete',
      rows_read: 3,
      rows_imported: 3,
      rows_merged: 0,
      rows_flagged: 0,
      rows_rejected: 0,
      error_code: null,
      error_detail: null,
      started_at: '2026-07-30T10:00:00Z',
      finished_at: '2026-07-30T10:00:01Z',
      duration_ms: 1000,
    },
    items_created: 3,
    items_updated: 0,
    items_removed: 0,
    sync: { started: false, run_id: null, reason: 'not_connected' },
    header_row_number: 1,
    detected_columns: { sku: 'SKU', quantity: 'Quantity' },
    rejected: [],
    duplicates: [],
    rejected_truncated: false,
    duplicates_truncated: false,
    sheet_format: 'aggregated',
    analysis: {
      skus_analyzed: 2,
      skus_matched: 1,
      skus_unmatched: 1,
      shopify_sales: 512,
      shopify_sales_pct: 3.4,
      total_complaints: 5,
    },
    unmapped_reasons: {},
  },
};

function routes(overrides: Record<string, Route> = {}) {
  const table: Record<string, Route> = {
    'GET /inventory/summary': {
      ok: true,
      status: 200,
      body: { total_skus: 1467, total_quantity: 62451, last_imported_at: null },
    },
    'POST /imports/upload': IMPORTED,
    // Every doorway returns the same ImportResult, so one fixture serves all.
    'POST /imports/google-sheet': IMPORTED,
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

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <ImportPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

function csv(name = 'stock.csv') {
  return new File(['SKU,Quantity\nDD-1,5\n'], name, { type: 'text/csv' });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('choosing a method', () => {
  it('opens on the three methods, not on a drop zone', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    expect(await screen.findByText('Choose a method')).toBeDefined();
    for (const title of ['CSV file', 'Excel file', 'Google Sheet']) {
      expect(screen.getByRole('button', { name: new RegExp(title) })).toBeDefined();
    }
    expect(container.querySelectorAll('.method')).toHaveLength(3);
    // The landing view no longer leads with the upload box.
    expect(container.querySelector('.drop')).toBeNull();
  });

  it.each(['CSV URL', 'Excel URL'])('no longer offers %s', async (title) => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Choose a method');
    expect(screen.queryByRole('button', { name: new RegExp(title) })).toBeNull();
    expect(screen.queryByText(new RegExp(title))).toBeNull();
  });

  it('gives Google Sheet the full-width row, with no Recommended badge', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    const wide = container.querySelector('.method.wide');
    expect(wide?.textContent).toContain('Google Sheet');
    expect(screen.queryByText('Recommended')).toBeNull();
  });

  it.each(['CSV file', 'Excel file', 'Google Sheet'])(
    '%s asks for a SKU and nothing else',
    async (title) => {
      vi.stubGlobal('fetch', routes());
      renderPage();

      await screen.findByText('Choose a method');
      await userEvent.click(screen.getByRole('button', { name: new RegExp(title) }));

      expect(await screen.findByText(/Only a SKU column is required/)).toBeDefined();
      // Column names are matched for the user, so the page must not demand any.
      expect(screen.queryByText(/Total Qty\. column/)).toBeNull();
      expect(screen.queryByText(/a quantity column/)).toBeNull();
    },
  );

  it('keeps Import history in the header', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    expect(await screen.findByRole('button', { name: /Import history/ })).toBeDefined();
  });

  it('announces which method is selected', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Choose a method');
    const card = screen.getByRole('button', { name: /CSV file/ });
    expect(card.getAttribute('aria-pressed')).toBe('false');

    await userEvent.click(card);

    expect(card.getAttribute('aria-pressed')).toBe('true');
  });
});

describe('the file methods', () => {
  it('shows the upload panel once CSV is chosen', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));

    expect(await screen.findByText('Upload a CSV file')).toBeDefined();
    expect(container.querySelector('.drop')).not.toBeNull();
  });

  it('Excel gets its own heading', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /Excel file/ }));

    expect(await screen.findByText('Upload an Excel file')).toBeDefined();
  });

  it('Change method returns to the grid', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await screen.findByText('Upload a CSV file');

    await userEvent.click(screen.getByRole('button', { name: 'Change method' }));

    expect(screen.queryByText('Upload a CSV file')).toBeNull();
    expect(screen.getByText('Choose a method')).toBeDefined();
  });

  it('uploads through the one unchanged endpoint', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    const input = container.querySelector('input[type=file]');
    await userEvent.upload(input as HTMLInputElement, csv());
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));

    await waitFor(() => {
      const posted = fetch.mock.calls.find(
        (call) => (call[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(String(posted?.[0])).toContain('/imports/upload');
      expect((posted?.[1] as RequestInit).body).toBeInstanceOf(FormData);
    });
  });

  it('keeps the import disabled until a file is chosen', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));

    expect(screen.getByRole('button', { name: 'Import file' }).hasAttribute('disabled')).toBe(
      true,
    );
  });

  it('shows the summary after a successful import', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));

    // The summary replaces the whole flow, method grid included.
    expect(await screen.findByText('stock.csv')).toBeDefined();
    expect(screen.getByText('SKUs imported')).toBeDefined();
    expect(screen.getByText('3 new · 0 updated')).toBeDefined();
    expect(screen.queryByText('Choose a method')).toBeNull();
  });

  it('says how many SKUs the import removed', async () => {
    /**
     * An import replaces the dataset, so a smaller file is a real removal. The
     * figure it changes — every number on the Dashboard — is on the next
     * screen, so this is the last chance to say it.
     */
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /imports/upload': {
          ok: true,
          status: 200,
          body: {
            ...(IMPORTED.body as Record<string, unknown>),
            items_removed: 1332,
          },
        },
      }),
    );
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));

    expect(await screen.findByText(/1,332 removed/)).toBeDefined();
  });

  it('stays quiet when the import removed nothing', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));

    await screen.findByText('stock.csv');
    expect(screen.queryByText(/removed/)).toBeNull();
  });

  it('reports the server’s message and next step on failure', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /imports/upload': {
          ok: false,
          status: 422,
          body: {
            error: {
              code: 'missing_headers',
              message: 'That sheet is missing a column StockSync Analytics needs.',
              next: 'Add the missing column, then upload again.',
            },
          },
        },
      }),
    );
    const { container } = renderPage();

    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));

    expect(await screen.findByText(/missing a column StockSync Analytics needs/)).toBeDefined();
    expect(screen.getByText(/Add the missing column/)).toBeDefined();
  });
});

describe('the Google Sheet method', () => {
  const LINK = 'https://docs.google.com/spreadsheets/d/1AbC_dEfGhIj/edit#gid=42';

  async function openSheetPanel() {
    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /Google Sheet/ }));
    return screen.findByLabelText(/Google Sheet link/);
  }

  async function connect(link = LINK) {
    const input = await openSheetPanel();
    await userEvent.type(input, link);
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }));
    return input;
  }

  /** A failure envelope from the sheet endpoint, shaped as the server sends it. */
  function failing(code: string, message: string, next: string) {
    return routes({
      'POST /imports/google-sheet': {
        ok: false,
        status: 422,
        body: { error: { code, message, next } },
      },
    });
  }

  it('shows the connection form', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    const input = await openSheetPanel();

    expect(screen.getByText('Connect a Google Sheet')).toBeDefined();
    expect(input).toBeDefined();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDefined();
    expect(
      screen.getByText(/You.ll be asked to grant read-only access to this sheet\./),
    ).toBeDefined();
    expect(container.querySelector('.drop')).toBeNull();
  });

  it('keeps Connect disabled until a link is entered', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    const input = await openSheetPanel();
    expect(screen.getByRole('button', { name: 'Connect' }).hasAttribute('disabled')).toBe(true);

    await userEvent.type(input, LINK);

    expect(screen.getByRole('button', { name: 'Connect' }).hasAttribute('disabled')).toBe(
      false,
    );
  });

  it('stays disabled for whitespace alone', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    const input = await openSheetPanel();
    await userEvent.type(input, '   ');

    expect(screen.getByRole('button', { name: 'Connect' }).hasAttribute('disabled')).toBe(true);
  });

  it('posts the link to the Google Sheet endpoint', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await connect(`  ${LINK}  `);

    await waitFor(() => {
      const posted = fetch.mock.calls.find((call) =>
        String(call[0]).includes('/imports/google-sheet'),
      );
      expect((posted?.[1] as RequestInit).method).toBe('POST');
      // Trimmed, and sent as JSON rather than multipart.
      expect(JSON.parse(String((posted?.[1] as RequestInit).body))).toEqual({ url: LINK });
    });
  });

  it('never posts to the file endpoint', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    await connect();

    await waitFor(() => {
      expect(fetch.mock.calls.some((call) => String(call[0]).includes('/imports/upload'))).toBe(
        false,
      );
    });
  });

  it('submits on Enter', async () => {
    const fetch = routes();
    vi.stubGlobal('fetch', fetch);
    renderPage();

    const input = await openSheetPanel();
    await userEvent.type(input, `${LINK}{Enter}`);

    await waitFor(() => {
      expect(
        fetch.mock.calls.some((call) => String(call[0]).includes('/imports/google-sheet')),
      ).toBe(true);
    });
  });

  it('shows the same summary a file import shows', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await connect();

    expect(await screen.findByText('stock.csv')).toBeDefined();
    expect(screen.getByText('SKUs imported')).toBeDefined();
    expect(screen.getByText('3 new · 0 updated')).toBeDefined();
    expect(screen.queryByText('Choose a method')).toBeNull();
  });

  it('shows the friendly message when the sheet is private', async () => {
    const message =
      "This Google Sheet isn't publicly accessible. Please make it viewable by " +
      'anyone with the link or upload it as a CSV file.';
    vi.stubGlobal(
      'fetch',
      failing(
        'sheet_not_public',
        message,
        'In Google Sheets: Share -> General access -> Anyone with the link -> Viewer.',
      ),
    );
    renderPage();

    await connect();

    expect(await screen.findByText(message)).toBeDefined();
    expect(screen.getByText(/Anyone with the link/)).toBeDefined();
  });

  it('shows the same validation message a CSV upload shows', async () => {
    vi.stubGlobal(
      'fetch',
      failing(
        'missing_headers',
        'That sheet is missing a column StockSync Analytics needs.',
        'Add the missing column, then upload again.',
      ),
    );
    renderPage();

    await connect();

    expect(await screen.findByText(/missing a column StockSync Analytics needs/)).toBeDefined();
    expect(screen.getByText(/Add the missing column/)).toBeDefined();
  });

  it('reports a link that is not a sheet', async () => {
    vi.stubGlobal(
      'fetch',
      failing(
        'not_a_google_sheet_url',
        'That does not look like a Google Sheet link.',
        'Open the sheet, copy the address from your browser, and paste the whole thing.',
      ),
    );
    renderPage();

    await connect('https://example.com/stock.csv');

    expect(await screen.findByText(/not look like a Google Sheet link/)).toBeDefined();
  });

  it('keeps the link after a failure so it can be corrected', async () => {
    vi.stubGlobal('fetch', failing('sheet_not_public', 'Not public.', 'Share it.'));
    renderPage();

    await connect();
    await screen.findByRole('alert');

    expect((await screen.findByLabelText(/Google Sheet link/)).getAttribute('value')).toBe(
      LINK,
    );
  });

  it('clears the message once the link is edited', async () => {
    vi.stubGlobal('fetch', failing('sheet_not_public', 'Not public.', 'Share it.'));
    renderPage();

    const input = await connect();
    await screen.findByRole('alert');

    await userEvent.type(input, '&range=A1');

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('no longer claims the integration is unavailable', async () => {
    vi.stubGlobal('fetch', routes());
    const { container } = renderPage();

    await connect();
    await screen.findByText('stock.csv');

    expect(container.textContent).not.toContain('currently unavailable');
  });

  it('Change method returns to the grid', async () => {
    vi.stubGlobal('fetch', routes());
    renderPage();

    await openSheetPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Change method' }));

    expect(screen.queryByText('Connect a Google Sheet')).toBeNull();
    expect(screen.getByText('Choose a method')).toBeDefined();
  });
});

describe('the summary of a grouped import', () => {
  /** What the server returns for a raw complaint sheet. */
  const grouped = (over: Record<string, unknown> = {}) =>
    routes({
      'POST /imports/upload': {
        ok: true,
        status: 200,
        body: {
          ...(IMPORTED.body as Record<string, unknown>),
          sheet_format: 'complaints',
          analysis: {
            skus_analyzed: 2,
            skus_matched: 1,
            skus_unmatched: 1,
            shopify_sales: 512,
            shopify_sales_pct: 3.4,
            total_complaints: 5,
          },
          detected_columns: { sku: 'SKU Code', reason: 'Issue Type', order_no: 'Order No' },
          duplicates: [{ sku: 'DD-1', rows: [2, 3, 4], merged_quantity: 3 }],
          unmapped_reasons: {},
          ...over,
        },
      },
    });

  async function importFile(fetchMock: ReturnType<typeof routes>) {
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderPage();
    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));
    return container;
  }

  it('says the sheet was read as complaint rows', async () => {
    await importFile(grouped());

    expect(await screen.findByText(/Read as complaint rows and grouped by SKU/)).toBeDefined();
  });

  it('calls the merge a grouping rather than a duplicate', async () => {
    await importFile(grouped());

    expect(await screen.findByText('SKUs grouped')).toBeDefined();
    expect(screen.queryByText('Duplicate SKUs merged')).toBeNull();
  });

  it('still calls it a duplicate for an aggregated sheet', async () => {
    await importFile(
      routes({
        'POST /imports/upload': {
          ok: true,
          status: 200,
          body: {
            ...(IMPORTED.body as Record<string, unknown>),
            duplicates: [{ sku: 'DD-1', rows: [2, 3], merged_quantity: 25 }],
          },
        },
      }),
    );

    expect(await screen.findByText('Duplicate SKUs merged')).toBeDefined();
  });

  it('lists reasons it could not place, and says they still counted', async () => {
    await importFile(grouped({ unmapped_reasons: { 'Late delivery': 4, 'Changed mind': 1 } }));

    expect(await screen.findByText('Reasons not recognised')).toBeDefined();
    expect(screen.getByText('Late delivery')).toBeDefined();
    expect(screen.getByText('Changed mind')).toBeDefined();
    // JSX wraps the sentence across lines, so read it off the panel itself
    // rather than matching text — ancestors match too, and getByText refuses.
    const panel = screen.getByText('Reasons not recognised').closest('.panel');
    expect(panel?.textContent).toContain('counted towards Total Count, Orders and Qty');
  });

  it('shows no such panel when every reason mapped', async () => {
    await importFile(grouped());

    await screen.findByText('SKUs grouped');
    expect(screen.queryByText('Reasons not recognised')).toBeNull();
  });

  it('reports the columns it matched, whatever they were called', async () => {
    await importFile(grouped());

    expect(await screen.findByText(/SKU Code → sku/)).toBeDefined();
    expect(screen.getByText(/Issue Type → reason/)).toBeDefined();
  });
});

describe('the import completes the analysis', () => {
  /** Choose CSV, attach a file, import — the path every summary test takes. */
  async function importAFile() {
    const { container } = renderPage();
    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));
  }

  it('states the outcome without a trip to the dashboard', async () => {
    /**
     * An import is not finished when the rows land — it is finished when those
     * SKUs have been matched against Shopify sales. The summary used to stop at
     * row counts, leaving the user to go and look.
     */
    vi.stubGlobal('fetch', routes());

    await importAFile();

    expect(await screen.findByText('Analysis complete')).toBeDefined();
    expect(screen.getByText(/1 of 2 SKUs matched a Shopify sale/)).toBeDefined();
  });

  it('offers the way through to the dashboard', async () => {
    vi.stubGlobal('fetch', routes());

    await importAFile();

    expect(await screen.findByRole('button', { name: /View dashboard/ })).toBeDefined();
  });
});

describe('a Date column that could not be used', () => {
  /**
   * The server recognises `date` as a column, so it appears in
   * `detected_columns` and the footer prints "matched Date → date" — which reads
   * as confirmation the dates were used. Without a Reason column they were not,
   * and the file was read as one row per SKU instead. Silence there is how a
   * per-day sheet ends up with its quantities summed across days and nobody
   * notices until a figure looks wrong weeks later.
   */
  const withWarnings = (warnings: string[], over: Record<string, unknown> = {}) =>
    routes({
      'POST /imports/upload': {
        ok: true,
        status: 200,
        body: {
          ...(IMPORTED.body as Record<string, unknown>),
          detected_columns: { sku: 'SKU', date: 'Date', total_qty: 'Total Qty.' },
          warnings,
          ...over,
        },
      },
    });

  async function importFile(fetchMock: ReturnType<typeof routes>) {
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderPage();
    await screen.findByText('Choose a method');
    await userEvent.click(screen.getByRole('button', { name: /CSV file/ }));
    await userEvent.upload(
      container.querySelector('input[type=file]') as HTMLInputElement,
      csv(),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Import file' }));
    return container;
  }

  it('says the Date column was not used', async () => {
    await importFile(withWarnings(['date_column_ignored']));

    await screen.findByText('Worth checking');
    expect(screen.getByText(/The Date column was not used\./)).toBeDefined();
    expect(screen.getByText(/no Reason column/)).toBeDefined();
  });

  it('names the merge when rows were combined', async () => {
    await importFile(
      withWarnings(['date_column_ignored_with_duplicates'], {
        duplicates: [{ sku: 'DD-1', rows: [2, 3], merged_quantity: 20 }],
      }),
    );

    await screen.findByText('Worth checking');
    expect(screen.getByText(/rows for the same SKU were combined/)).toBeDefined();
    expect(screen.getByText(/summed across days/)).toBeDefined();
  });

  it('tells the user what to do instead', async () => {
    await importFile(withWarnings(['date_column_ignored']));

    await screen.findByText('Worth checking');
    expect(screen.getByText(/import the complaint export/)).toBeDefined();
  });

  it('says nothing when there is nothing to say', async () => {
    await importFile(withWarnings([]));

    await screen.findByText('stock.csv');
    expect(screen.queryByText('Worth checking')).toBeNull();
  });

  it('survives a response from a server that predates the field', async () => {
    /** A cached bundle can outlive a deploy in either direction. */
    await importFile(
      routes({
        'POST /imports/upload': {
          ok: true,
          status: 200,
          body: { ...(IMPORTED.body as Record<string, unknown>) },
        },
      }),
    );

    await screen.findByText('stock.csv');
    expect(screen.queryByText('Worth checking')).toBeNull();
  });
});
