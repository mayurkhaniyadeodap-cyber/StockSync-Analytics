// @vitest-environment jsdom
/**
 * The one banner the Dashboard and every Analytics page now share.
 *
 * The rule worth pinning is the precedence: a sync commits orders page by page
 * and recomputes the rollup at the end, so every run passes through a window
 * where orders exist that the rollup has not seen. Both flags are true then.
 * Showing "Sales figures are behind" during it put a Retry on screen that the
 * server would have refused, because a sync was already running.
 */

import { cleanup, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import { SyncStateNotice } from './SyncStateNotice';
import { ToastProvider } from '../contexts/ToastContext';

afterEach(cleanup);

const noop = () => {};

/**
 * The stale branch renders RetrySyncButton, which raises a toast on failure.
 *
 * Used only where it is needed: ToastProvider mounts its own live region, and
 * a test asserting "renders nothing" or "there is one status role" would end up
 * measuring the provider rather than this component.
 */
function withToasts(node: ReactNode) {
  return render(<ToastProvider>{node}</ToastProvider>);
}

describe('when neither state applies', () => {
  it('renders nothing', () => {
    const { container } = render(
      <SyncStateNotice syncing={false} stale={false} onRetryStarted={noop} />,
    );

    expect(container.firstChild).toBeNull();
  });

  it('renders nothing before the figures have loaded', () => {
    /** Both flags are undefined on the first paint; a banner that flashed then
        would appear on every navigation. */
    const { container } = render(
      <SyncStateNotice syncing={undefined} stale={undefined} onRetryStarted={noop} />,
    );

    expect(container.firstChild).toBeNull();
  });
});

describe('while a sync is running', () => {
  it('says so', () => {
    render(<SyncStateNotice syncing stale={false} onRetryStarted={noop} />);

    expect(screen.getByText('Sync in progress…')).toBeDefined();
  });

  it('reports it as information rather than as a problem', () => {
    /** Slate, not rust: a sync running is not something wrong. */
    const { container } = render(
      <SyncStateNotice syncing stale={false} onRetryStarted={noop} />,
    );

    expect(container.querySelector('.inline-err.info')).not.toBeNull();
    expect(screen.getByRole('status')).toBeDefined();
  });
});

describe('when the figures are behind', () => {
  it('says so and offers a retry', () => {
    const { container } = withToasts(
      <SyncStateNotice syncing={false} stale onRetryStarted={noop} />,
    );

    expect(screen.getByText('Sales figures are behind the last sync.')).toBeDefined();
    expect(within(container).getByRole('button', { name: /Retry sync/ })).toBeDefined();
  });

  it('is a warning, not a progress status', () => {
    const { container } = withToasts(
      <SyncStateNotice syncing={false} stale onRetryStarted={noop} />,
    );

    expect(container.querySelector('.inline-err.info')).toBeNull();
  });
});

describe('when both are true', () => {
  it('prefers the sync notice', () => {
    render(<SyncStateNotice syncing stale onRetryStarted={noop} />);

    expect(screen.getByText('Sync in progress…')).toBeDefined();
    expect(screen.queryByText(/behind the last sync/)).toBeNull();
  });

  it('offers no retry, because the server would refuse it', () => {
    /** The bug this precedence fixed: a Retry button beside a running sync,
        which the server answers with 409. */
    render(<SyncStateNotice syncing stale onRetryStarted={noop} />);

    expect(screen.queryByRole('button', { name: /Retry sync/ })).toBeNull();
  });
});
