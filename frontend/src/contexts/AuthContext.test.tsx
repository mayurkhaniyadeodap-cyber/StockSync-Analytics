// @vitest-environment jsdom
/**
 * Session handling across a boot, an expiry and a sign-out.
 *
 * The bug these were written for: the access cookie lives fifteen minutes and
 * the refresh cookie thirty days, and nothing ever spent the second one. A tab
 * open past the quarter hour answered "You're not signed in." to whatever the
 * user did next; reopening the app after lunch showed the login form even
 * though the browser was still holding a perfectly good refresh cookie.
 *
 * What is asserted here is the provider's part of that: a 401 on boot is a
 * question, not an answer, and a session that really has ended says so out loud
 * instead of leaving a shell full of failed panels.
 */

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from './AuthContext';
import { useAuth } from '../hooks/useAuth';
import { clearSessionExpiry } from '../lib/api';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearSessionExpiry();
});

const USER = {
  id: 1,
  email: 'admin@deodap.in',
  full_name: 'Administrator',
  role: 'Admin',
  timezone: 'Asia/Kolkata',
  initials: 'AD',
  workspace: {
    id: 1,
    name: 'Deodap Retail',
    slug: 'deodap',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    low_stock_threshold: 10,
  },
  preferences: { theme: 'light', table_density: 'comfortable', alert_on_stockout: true },
  access_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
};

const REFUSED = {
  ok: false,
  status: 401,
  json: () =>
    Promise.resolve({
      error: {
        code: 'not_authenticated',
        message: "You're not signed in.",
        next: 'Sign in and try again.',
      },
    }),
} as Response;

const accepted = (body: unknown = USER) =>
  ({ ok: true, status: 200, json: () => Promise.resolve(body) }) as Response;

/** Answers each call from a script and records where it went. */
function scripted(replies: Response[]) {
  const paths: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      paths.push(String(input));
      return Promise.resolve(
        replies[paths.length - 1] ?? replies[replies.length - 1] ?? REFUSED,
      );
    }),
  );
  return paths;
}

/** Renders the provider's status, which is all the router reads. */
function Probe() {
  const { status, user } = useAuth();
  return <div data-testid="status">{user ? `${status}:${user.initials}` : status}</div>;
}

const show = () =>
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );

const settled = () =>
  waitFor(() => {
    expect(screen.getByTestId('status').textContent).not.toBe('checking');
  });

describe('on boot', () => {
  it('holds at checking until the server answers', () => {
    scripted([]);
    // Deliberately not awaited: this is the frame the router draws the boot
    // screen on, and drawing the login card here is the flash the gate exists
    // to prevent.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    );

    show();

    expect(screen.getByTestId('status').textContent).toBe('checking');
  });

  it('signs in from a cookie the page cannot read', async () => {
    scripted([accepted()]);

    show();
    await settled();

    expect(screen.getByTestId('status').textContent).toBe('authenticated:AD');
  });

  it('renews an expired access token rather than showing the login form', async () => {
    /*
     * Reopening the application after lunch. The access cookie is fifteen
     * minutes gone; the refresh cookie is good for thirty days. Before this,
     * the 401 was taken as the answer and the user was sent to sign in again.
     */
    const paths = scripted([REFUSED, accepted(), accepted()]);

    show();
    await settled();

    expect(screen.getByTestId('status').textContent).toBe('authenticated:AD');
    expect(paths).toEqual(['/api/auth/me', '/api/auth/refresh', '/api/auth/me']);
  });

  it('settles on anonymous when there is no session to renew', async () => {
    // A first visit. One wasted refresh attempt, then the login screen — the
    // page cannot read the httpOnly cookie to know there is nothing to try.
    scripted([REFUSED, REFUSED]);

    show();
    await settled();

    expect(screen.getByTestId('status').textContent).toBe('anonymous');
  });
});

describe('the renewal schedule', () => {
  /** Boot the provider on a fake clock and return where it has been. */
  async function boot(expiresInMs: number) {
    const paths = scripted([
      accepted({
        ...USER,
        access_expires_at: new Date(Date.now() + expiresInMs).toISOString(),
      }),
      accepted({ ...USER, access_expires_at: new Date(Date.now() + 3_600_000).toISOString() }),
    ]);
    show();
    // Flushes the boot request; `waitFor` cannot be used here because it polls
    // on the very timers this test controls.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('status').textContent).toBe('authenticated:AD');
    return paths;
  }

  const wait = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });

  it('does not renew while the token still has time on it', async () => {
    /*
     * The timer is capped at ten minutes so a suspended machine or a jumped
     * clock cannot park it, which means it wakes well before a token is due.
     * Renewing on the wake rather than on the expiry would rotate the session
     * on a schedule of its own.
     */
    vi.useFakeTimers();
    try {
      const paths = await boot(60 * 60_000);

      await wait(11 * 60_000); // past the cap, nowhere near the expiry

      expect(paths.filter((p) => p === '/api/auth/refresh')).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('renews a minute before the token dies, without a request failing first', async () => {
    vi.useFakeTimers();
    try {
      const paths = await boot(15 * 60_000);

      await wait(14 * 60_000 + 1_000);

      expect(paths).toContain('/api/auth/refresh');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('when a session ends mid-use', () => {
  it('drops to anonymous so the router can redirect to the login page', async () => {
    scripted([accepted()]);
    show();
    await settled();

    // Something the user does next — a filter, a poll, a report. The renewal
    // is refused because the session was revoked or the account deactivated.
    scripted([REFUSED, REFUSED]);
    const { api } = await import('../lib/api');
    await api.get('/analytics/kpis').catch(() => undefined);

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('anonymous');
    });
  });

  it('does not fail silently, leaving a signed-in shell behind', async () => {
    /*
     * The old behaviour: `status` stayed 'authenticated' because nothing told
     * the provider, so every panel rendered its own "couldn't load this" and
     * the actual problem — you are signed out — went unsaid.
     */
    scripted([accepted()]);
    show();
    await settled();
    expect(screen.getByTestId('status').textContent).toBe('authenticated:AD');

    scripted([REFUSED, REFUSED]);
    const { api } = await import('../lib/api');
    await api.get('/reports').catch(() => undefined);

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).not.toContain('authenticated');
    });
  });
});

describe('when the server cannot answer', () => {
  /**
   * The reported bug, at the level the user felt it: signed out moments after
   * an automatic sync that had worked.
   *
   * `/auth/refresh` writes — it inserts a session row and revokes the old one.
   * The rollup rebuild that follows a sync holds SQLite's single write lock for
   * 74 seconds on the live database, against a 10-second busy timeout. A
   * renewal landing in that window came back 500, and every non-2xx was read as
   * "session over".
   */
  const SERVER_ERROR = {
    ok: false,
    status: 500,
    json: () =>
      Promise.resolve({
        error: {
          code: 'internal_error',
          message: 'Something went wrong on our side.',
          next: 'Try again.',
        },
      }),
  } as Response;

  it('keeps a signed-in user signed in', async () => {
    scripted([accepted()]);
    show();
    await settled();
    expect(screen.getByTestId('status').textContent).toBe('authenticated:AD');

    // The dashboard refresh after the workflow: 401, then a renewal the busy
    // database cannot serve.
    scripted([REFUSED, SERVER_ERROR]);
    const { api } = await import('../lib/api');
    await api.get('/analytics/kpis').catch(() => undefined);

    // Still signed in, still on the same page.
    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('authenticated:AD');
    });
  });

  it('still signs out when the backend confirms the session is invalid', async () => {
    // The guard must not swallow a real expiry.
    scripted([accepted()]);
    show();
    await settled();

    scripted([REFUSED, REFUSED]);
    const { api } = await import('../lib/api');
    await api.get('/analytics/kpis').catch(() => undefined);

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('anonymous');
    });
  });

  it('says unreachable rather than anonymous when a boot cannot be answered', async () => {
    // Nothing to keep signed in, but "could not tell" is still not "signed
    // out", and the router shows a retry rather than the login form.
    scripted([REFUSED, SERVER_ERROR]);

    show();

    await waitFor(() => {
      expect(screen.getByTestId('status').textContent).toBe('unreachable');
    });
  });

  it('a first visit with no session is still anonymous', async () => {
    // Both refused: the server answered, and the answer was no.
    scripted([REFUSED, REFUSED]);

    show();
    await settled();

    expect(screen.getByTestId('status').textContent).toBe('anonymous');
  });
});
