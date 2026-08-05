// @vitest-environment jsdom
/**
 * The step log under a run in Sync History.
 *
 * A run row says what it ended up as. When one comes back partial that is a
 * single error string and no account of how far it got, which is what these
 * steps supply.
 */

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SyncSteps, SyncStepsToggle } from './SyncSteps';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const STEPS = [
  { step: 'import_started', state: 'ok', detail: 'stock.csv', at: '2026-08-04T10:00:00Z' },
  { step: 'inventory_imported', state: 'ok', detail: '3 new', at: '2026-08-04T10:00:01Z' },
  { step: 'sync_started', state: 'started', detail: null, at: '2026-08-04T10:00:02Z' },
  { step: 'sync_completed', state: 'ok', detail: '40 orders', at: '2026-08-04T10:01:00Z' },
  { step: 'recompute_started', state: 'started', detail: null, at: '2026-08-04T10:01:01Z' },
  {
    step: 'recompute_failed',
    state: 'failed',
    detail: 'The figures could not be recomputed.',
    at: '2026-08-04T10:01:02Z',
  },
  {
    step: 'workflow_finished',
    state: 'failed',
    detail: 'Final status: partial',
    at: '2026-08-04T10:01:03Z',
  },
];

function serving(body: unknown, ok = true) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok,
        status: ok ? 200 : 500,
        json: () => Promise.resolve(body),
      } as Response),
    ),
  );
}

describe('the step log', () => {
  it('words every identifier the server sends', async () => {
    serving(STEPS);
    render(<SyncSteps runId={1} />);

    for (const label of [
      'Import started',
      'Inventory imported',
      'Shopify sync started',
      'Shopify sync completed',
      'Analytics recompute started',
      'Analytics recompute failed',
      'Final status',
    ]) {
      expect(await screen.findByText(label)).toBeDefined();
    }
  });

  it('names which stage failed, rather than leaving it to be inferred', async () => {
    serving(STEPS);
    render(<SyncSteps runId={1} />);

    expect(await screen.findByText('Analytics recompute failed')).toBeDefined();
    expect(screen.queryByText('Analytics recompute completed')).toBeNull();
  });

  it('carries the detail each step recorded', async () => {
    serving(STEPS);
    render(<SyncSteps runId={1} />);

    expect(await screen.findByText(/stock\.csv/)).toBeDefined();
    expect(screen.getByText(/40 orders/)).toBeDefined();
  });

  it('keeps the order the steps happened in', async () => {
    serving(STEPS);
    const { container } = render(<SyncSteps runId={1} />);

    await screen.findByText('Import started');
    const shown = Array.from(container.querySelectorAll('li b')).map((b) => b.textContent);
    expect(shown[0]).toBe('Import started');
    expect(shown[shown.length - 1]).toBe('Final status');
  });

  it('says a run has no steps rather than showing an empty list', async () => {
    // Runs from before the log existed. Not an error.
    serving([]);
    render(<SyncSteps runId={1} />);

    expect(await screen.findByText('No steps recorded for this run.')).toBeDefined();
  });

  it('does not pass a load failure off as a run with no steps', async () => {
    serving({}, false);
    render(<SyncSteps runId={1} />);

    expect(await screen.findByText('Could not load the steps for this run.')).toBeDefined();
  });
});

describe('the toggle', () => {
  it('fetches nothing until it is opened', async () => {
    serving(STEPS);
    render(<SyncStepsToggle runId={7} />);

    expect(globalThis.fetch).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: /Steps/ }));

    expect(await screen.findByText('Import started')).toBeDefined();
  });

  it('closes again', async () => {
    serving(STEPS);
    render(<SyncStepsToggle runId={7} />);

    await userEvent.click(screen.getByRole('button', { name: /Steps/ }));
    await screen.findByText('Import started');
    await userEvent.click(screen.getByRole('button', { name: /Hide steps/ }));

    expect(screen.queryByText('Import started')).toBeNull();
  });
});
