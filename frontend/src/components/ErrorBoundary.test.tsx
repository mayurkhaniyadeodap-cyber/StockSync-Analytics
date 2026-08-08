// @vitest-environment jsdom
/**
 * The boundary that stops a render error becoming a blank page.
 *
 * Before it existed, one throw anywhere unmounted the whole tree: no message,
 * no navigation, nothing but white. These tests hold the two halves of the
 * contract — a working tree is passed through untouched, and a broken one gets
 * a page a person can act on.
 */

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from './ErrorBoundary';

function Boom({ message = 'kaboom' }: { message?: string }): never {
  throw new Error(message);
}

beforeEach(() => {
  // React logs the caught error itself; silencing keeps the run readable
  // without hiding the assertion that our own handler ran.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('when nothing is wrong', () => {
  it('renders its children untouched', () => {
    render(
      <ErrorBoundary>
        <p>the actual page</p>
      </ErrorBoundary>,
    );

    expect(screen.getByText('the actual page')).toBeDefined();
  });
});

describe('when a child throws', () => {
  it('shows the fallback instead of an empty document', () => {
    const { container } = render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText('Something went wrong')).toBeDefined();
    expect(container.textContent).not.toBe('');
  });

  it('announces itself, because the content changed without a navigation', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toBeDefined();
  });

  it('logs the error and the component stack for whoever is debugging', () => {
    render(
      <ErrorBoundary>
        <Boom message="something specific" />
      </ErrorBoundary>,
    );

    const logged = (console.error as ReturnType<typeof vi.fn>).mock.calls;
    expect(logged.some((args) => String(args[0]).includes('[StockSync] render error'))).toBe(
      true,
    );
  });

  it('shows the message but never a stack trace', () => {
    render(
      <ErrorBoundary>
        <Boom message="Cannot read properties of undefined" />
      </ErrorBoundary>,
    );

    expect(screen.getByText('Cannot read properties of undefined')).toBeDefined();
    expect(document.body.textContent).not.toContain('at Boom');
  });

  it('offers a reload', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, href: 'http://x/reports', assign });

    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByRole('button', { name: /Reload/ }));

    expect(assign).toHaveBeenCalledWith('http://x/reports');
  });

  it('offers a way out when given one', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });

    render(
      <ErrorBoundary home="/dashboard">
        <Boom />
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByRole('button', { name: /Back to dashboard/ }));

    // A document load, not in-app routing: the tree that threw still holds
    // whatever bad state caused it, so only a rebuild is trustworthy.
    expect(assign).toHaveBeenCalledWith('/dashboard');
  });

  it('hides the way out when there is nowhere useful to go', () => {
    /** The outermost boundary sits above the router — "dashboard" there would
        be a link into a provider tree that is already broken. */
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.queryByRole('button', { name: /Back to dashboard/ })).toBeNull();
  });

  it('names the scope it was given', () => {
    render(
      <ErrorBoundary scope="This page">
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText(/This page stopped unexpectedly/)).toBeDefined();
  });

  it('says the application when it has no narrower scope', () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText(/The application stopped unexpectedly/)).toBeDefined();
  });

  it('reassures rather than alarms', () => {
    /** A render error loses no data — saying so is the difference between a
        bug report and a panicked call about lost inventory. */
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText(/Nothing you had saved is affected/)).toBeDefined();
  });
});

describe('remounting', () => {
  it('shows the recovered tree when React gives it a fresh instance', () => {
    /** How AppShell recovers: the boundary is keyed on the pathname, so a
        navigation replaces the instance rather than reusing a latched one.
        Without the key, one broken page would show its fallback forever. */
    const { rerender } = render(
      <ErrorBoundary key="/reports">
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeDefined();

    rerender(
      <ErrorBoundary key="/dashboard">
        <p>a different page</p>
      </ErrorBoundary>,
    );

    expect(screen.getByText('a different page')).toBeDefined();
    expect(screen.queryByText('Something went wrong')).toBeNull();
  });
});
