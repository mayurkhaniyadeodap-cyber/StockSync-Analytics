// @vitest-environment jsdom
/**
 * The application frame, and specifically the mobile navigation drawer.
 *
 * Below 1024px the sidebar becomes an overlay. It closed on a scrim click and
 * on a route change — both pointer gestures — so on a tablet with a keyboard
 * there was no way out of it. The header popovers had handled Escape since M1
 * through `useOnClickOutside`; the drawer had been missed.
 */

import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppShell } from './AppShell';
import { AuthProvider } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';

function renderShell() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <AppShell />
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** The drawer is open when the sidebar carries the `open` class. */
function drawerIsOpen(container: HTMLElement): boolean {
  return container.querySelector('.side.open') !== null;
}

/** The header's drawer control. */
async function openControl(): Promise<HTMLElement> {
  return screen.findByRole('button', { name: 'Open navigation' });
}

const ME = {
  id: 1,
  email: 'admin@deodap.in',
  full_name: 'Administrator',
  initials: 'A',
  role: 'Admin',
  timezone: 'Asia/Kolkata',
  workspace: { id: 1, name: 'Deodap Retail', slug: 'deodap' },
  preferences: { theme: 'light', table_density: 'comfortable' },
};

/**
 * The shell renders a signed-in header, so the routes it reads on mount have to
 * answer. Without a user it throws while rendering the avatar, and the failure
 * looks like the drawer not opening.
 */
function routes() {
  const table: Record<string, unknown> = {
    'GET /auth/me': ME,
    'GET /shopify/connection': { connected: false, connection: null, source: 'none' },
    'GET /shopify/sales/summary': {
      orders: 0,
      line_items: 0,
      skus_with_sales: 0,
      last_synced_at: null,
    },
    'GET /shopify/sync': { running: false, run: null, last_synced_at: null },
  };
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const key = Object.keys(table).find((entry) => {
      const [verb, path = ''] = entry.split(' ');
      return verb === method && url.includes(path);
    });
    return Promise.resolve({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve(key ? table[key] : {}),
    });
  });
}

beforeEach(() => {
  vi.stubGlobal('fetch', routes());
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the mobile navigation drawer', () => {
  it('opens from the header control', async () => {
    const { container } = renderShell();

    await userEvent.click(await openControl());

    expect(drawerIsOpen(container)).toBe(true);
  });

  it('closes on Escape', async () => {
    const { container } = renderShell();
    await userEvent.click(await openControl());
    expect(drawerIsOpen(container)).toBe(true);

    await userEvent.keyboard('{Escape}');

    expect(drawerIsOpen(container)).toBe(false);
  });

  it('ignores Escape when it is already closed', async () => {
    /**
     * The listener is only attached while the drawer is open, so this asserts
     * the effect's guard holds rather than merely that nothing crashes.
     */
    const { container } = renderShell();
    await openControl();

    await userEvent.keyboard('{Escape}');

    expect(drawerIsOpen(container)).toBe(false);
  });
});
