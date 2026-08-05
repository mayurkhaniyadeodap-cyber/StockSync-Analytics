// @vitest-environment jsdom
/**
 * The four things the import screen can say about the sync that follows it.
 *
 * The state that matters most is failure: the rows are in either way, and a
 * message that reads like a failed import would send someone to upload the file
 * again when what they need is to retry the sync.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SyncAfterImport } from './SyncAfterImport';
import { ToastProvider } from '../contexts/ToastContext';
import type { SyncRun } from '../types/api';

/** RetrySyncButton reports a refused retry through a toast. */
function show(sync: Parameters<typeof SyncAfterImport>[0]['sync']) {
  return render(
    <ToastProvider>
      <SyncAfterImport sync={sync} />
    </ToastProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const run = (over: Partial<SyncRun> = {}): SyncRun => ({
  id: 1,
  trigger: 'import',
  status: 'finished',
  stage: 'done',
  orders_pct: 100,
  orders_synced: 1234,
  line_items_synced: 5678,
  result: 'success',
  error_code: null,
  error_detail: null,
  retry_after_seconds: null,
  started_at: '2026-08-04T10:00:00Z',
  finished_at: '2026-08-04T10:02:00Z',
  duration_ms: 120000,
  is_running: false,
  ...over,
});

/** `GET /shopify/sync` answers with this state; POST is recorded. */
function serving(state: { running: boolean; run: SyncRun | null }) {
  const posts: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET').toUpperCase() === 'POST') posts.push(String(input));
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...state, last_synced_at: null }),
      } as Response);
    }),
  );
  return posts;
}

const started = { started: true, run_id: 1, reason: null };

describe('while the sync is running', () => {
  it('says so, and says the figures will catch up', async () => {
    serving({ running: true, run: run({ is_running: true, result: null, status: 'running' }) });
    render(<SyncAfterImport sync={started} />);

    const note = await screen.findByRole('status');
    expect(note.textContent).toContain('Syncing Shopify sales…');
    expect(note.textContent).toContain('will update when it finishes');
  });

  it('tells the user they can leave the page', async () => {
    serving({ running: true, run: run({ is_running: true, result: null, status: 'running' }) });
    render(<SyncAfterImport sync={started} />);

    expect((await screen.findByRole('status')).textContent).toContain('keeps running');
  });
});

describe('when the sync succeeds', () => {
  it('confirms the figures are current and says how much came through', async () => {
    serving({ running: false, run: run() });
    render(<SyncAfterImport sync={started} />);

    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('Shopify sales are up to date'),
    );
    expect(screen.getByRole('status').textContent).toContain('1,234 orders synced');
  });

  it('offers no retry, because there is nothing to retry', async () => {
    serving({ running: false, run: run() });
    render(<SyncAfterImport sync={started} />);

    await waitFor(() => expect(screen.getByRole('status')).toBeDefined());
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull();
  });
});

describe('when the sync fails', () => {
  const failed = run({
    result: 'failed',
    status: 'finished',
    error_code: 'shopify_unreachable',
    error_detail: 'Shopify could not be reached.',
  });

  it('says the import is safe before it says anything went wrong', async () => {
    /** A failed sync is not a failed import, and the message must not read as
        one — the rows are in; only the sales beside them are behind. */
    serving({ running: false, run: failed });
    show(started);

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Your import is saved.');
    expect(alert.textContent).toContain('Shopify could not be reached.');
  });

  it('retries only what failed, without asking for the file again', async () => {
    const posts = serving({ running: false, run: failed });
    show(started);
    await screen.findByRole('alert');

    await userEvent.click(screen.getByRole('button', { name: /Retry sync/ }));

    // The retry endpoint, not a plain sync: the server decides whether to
    // re-fetch from Shopify or to recompute over orders already downloaded.
    await waitFor(() =>
      expect(posts.some((p) => p.includes('/shopify/sync/retry'))).toBe(true),
    );
    expect(posts.some((p) => p.includes('/imports'))).toBe(false);
  });

  it('treats a partial run as saved-but-behind, with the reason', async () => {
    /** The shape a failed recompute takes: the orders arrived, the figures
        did not. A retry away, and emphatically not a re-import. */
    serving({
      running: false,
      run: run({
        result: 'partial',
        error_code: 'rollup_failed',
        error_detail: 'Orders synced, but the figures could not be recomputed.',
      }),
    });
    show(started);

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Your import is saved.');
    expect(alert.textContent).toContain('could not be recomputed');
    expect(screen.getByRole('button', { name: /Retry sync/ })).toBeDefined();
  });
});

describe('when no sync was started', () => {
  it('explains an unconnected store without calling it an error', async () => {
    serving({ running: false, run: null });
    render(
      <SyncAfterImport sync={{ started: false, run_id: null, reason: 'not_connected' }} />,
    );

    const note = await screen.findByRole('status');
    expect(note.textContent).toContain('Shopify is not connected');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says a run already in flight covers this import', async () => {
    serving({ running: true, run: run({ is_running: true, result: null }) });
    render(
      <SyncAfterImport sync={{ started: false, run_id: null, reason: 'already_running' }} />,
    );

    const note = await screen.findByRole('status');
    expect(note.textContent).toContain('already running');
    expect(note.textContent).toContain('no second sync was started');
  });
});
