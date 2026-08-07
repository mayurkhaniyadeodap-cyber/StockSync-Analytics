// @vitest-environment jsdom
/**
 * The hook behind every "is a sync running" answer in the app.
 *
 * The case these tests exist for: a sync this hook did not start. The server
 * queues one after every successful import, so the shared instance in
 * `ShopifyStatusContext` — mounted once with the app, against an idle server —
 * has to notice a run appearing without being told. It previously did not: it
 * polled only after it had already seen `running`, so an idle mount stopped
 * looking for good. Nothing then published a completion, and the dashboard's
 * "Sync in progress…" banner had no signal to clear it short of a page reload.
 */

import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSync } from './useSync';
import type { SyncState } from '../types/api';

const IDLE = 5000;
const RUNNING = 1500;

/**
 * Advance the clock and let React settle.
 *
 * `act` is not optional here: the hook's state lands from a resolved fetch, so
 * without it `result.current` is read before the render that used the response.
 */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function state(running: boolean): SyncState {
  return {
    running,
    run: {
      id: 1,
      status: running ? 'running' : 'finished',
      stage: running ? 'orders' : 'done',
      result: running ? null : 'success',
      is_running: running,
      orders_pct: running ? 40 : 100,
      orders_synced: running ? 100 : 250,
      line_items_synced: running ? 200 : 500,
      error_code: null,
      error_detail: null,
      retry_after_seconds: null,
      trigger: 'import',
      started_at: '2026-08-06T11:45:00Z',
      finished_at: running ? null : '2026-08-06T11:58:00Z',
      duration_ms: running ? null : 780000,
    },
    last_synced_at: running ? null : '2026-08-06T11:58:00Z',
  } as unknown as SyncState;
}

describe('useSync', () => {
  let live = state(false);

  beforeEach(() => {
    vi.useFakeTimers();
    live = state(false);
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(live),
        } as Response),
      ),
    );
  });

  afterEach(() => {
    // Before the timers go back to real: a hook left mounted keeps its interval,
    // and its polls land on the *next* test's fetch mock.
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('keeps polling after mounting against an idle server', async () => {
    const { result } = renderHook(() => useSync(true));
    await advance(0);
    expect(result.current.state?.running).toBe(false);

    // A sync begins somewhere else entirely — an import, or another tab.
    live = state(true);
    await advance(IDLE);

    // The old hook had stopped looking and would still report false here.
    expect(result.current.state?.running).toBe(true);
  });

  it('signals completion for a sync it never started', async () => {
    const { result } = renderHook(() => useSync(true));
    await advance(0);

    live = state(true);
    await advance(IDLE);
    expect(result.current.completedAt).toBe(0);

    live = state(false);
    await advance(RUNNING);

    // What `ShopifyStatusContext` watches to bump `changedAt`, which is what
    // makes the dashboard re-read its figures and drop the banner.
    expect(result.current.completedAt).toBeGreaterThan(0);
  });

  it('polls faster while a run is in flight than between runs', async () => {
    const calls = () => (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length;

    const { result } = renderHook(() => useSync(true));
    await advance(0);

    const afterMount = calls();
    await advance(RUNNING);
    // Idle: the running cadence has passed and nothing was asked.
    expect(calls()).toBe(afterMount);

    live = state(true);
    await advance(IDLE);
    expect(result.current.state?.running).toBe(true);

    const afterStart = calls();
    await advance(RUNNING);
    expect(calls()).toBeGreaterThan(afterStart);
  });

  it('does not poll when disabled', async () => {
    renderHook(() => useSync(false));
    await advance(IDLE * 3);

    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0);
  });

  it('clears a stale error once a poll succeeds again', async () => {
    const failing = vi.fn(() => Promise.reject(new Error('offline')));
    vi.stubGlobal('fetch', failing);

    const { result } = renderHook(() => useSync(true));
    await advance(0);
    expect(result.current.error).not.toBeNull();

    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(live),
        } as Response),
      ),
    );
    await advance(IDLE);

    // Without this the hook now polls forever, so one dropped request would
    // leave an error on screen for the life of the page.
    expect(result.current.error).toBeNull();
  });
});
