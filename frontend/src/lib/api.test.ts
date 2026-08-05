/** The API client's request shaping — the part every page depends on. */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  StockSyncApiError,
  api,
  clearRequestLog,
  clearSessionExpiry,
  ensureSession,
  onSessionExpired,
  onSessionUnavailable,
  refreshSession,
  request,
  requestLog,
  sessionExpiresAt,
} from './api';

type FetchMock = ReturnType<typeof makeFetchMock>;

function makeFetchMock(response: Partial<Response>) {
  // The parameters are declared rather than inferred: without them vitest types
  // the mock's calls as an empty tuple and every `calls[0][1]` is an error
  // under noUncheckedIndexedAccess.
  return vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(response as Response),
  );
}

function stub(response: Partial<Response> & { json?: () => Promise<unknown> }): FetchMock {
  const mock = makeFetchMock(response);
  vi.stubGlobal('fetch', mock);
  return mock;
}

/** The RequestInit of the first fetch call, or a clear failure. */
function firstInit(mock: FetchMock): RequestInit {
  const call = mock.mock.calls[0];
  if (!call) throw new Error('fetch was never called');
  return call[1] ?? {};
}

function firstHeaders(mock: FetchMock): Record<string, string> {
  return (firstInit(mock).headers ?? {}) as Record<string, string>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // Module-level session state outlives a test otherwise.
  clearSessionExpiry();
  onSessionExpired(() => {});
  onSessionUnavailable(() => {});
  clearRequestLog();
});

/* ------------------------------------------------------------------ helpers */

interface Reply {
  status: number;
  body?: unknown;
}

const ok = (body: unknown = {}): Reply => ({ status: 200, body });
const unauthorised = (): Reply => ({
  status: 401,
  body: {
    error: {
      code: 'not_authenticated',
      message: "You're not signed in.",
      next: 'Sign in and try again.',
    },
  },
});

/** A fetch that answers each call from a script, and records the paths. */
function scripted(replies: Reply[]) {
  const paths: string[] = [];
  const mock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    paths.push(String(input));
    const reply = replies[paths.length - 1] ?? replies[replies.length - 1];
    if (!reply) throw new Error('no reply scripted');
    return Promise.resolve({
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: () => Promise.resolve(reply.body ?? {}),
    } as Response);
  });
  vi.stubGlobal('fetch', mock);
  return { mock, paths };
}

const IN_AN_HOUR = () => new Date(Date.now() + 3_600_000).toISOString();

describe('request', () => {
  it('sends credentials so the httpOnly auth cookie travels', async () => {
    const mock = stub({ ok: true, status: 200, json: () => Promise.resolve({}) });

    await api.get('/thing');

    expect(firstInit(mock).credentials).toBe('include');
  });

  it('sets a JSON content type for a JSON body', async () => {
    const mock = stub({ ok: true, status: 200, json: () => Promise.resolve({}) });

    await api.post('/thing', { a: 1 });

    expect(firstHeaders(mock)['Content-Type']).toBe('application/json');
  });

  it('unwraps the error envelope into message and next step', async () => {
    stub({
      ok: false,
      status: 400,
      json: () =>
        Promise.resolve({
          error: { code: 'shopify_auth_failed', message: 'Nope.', next: 'Fix the token.' },
        }),
    });

    await expect(api.get('/thing')).rejects.toMatchObject({
      code: 'shopify_auth_failed',
      message: 'Nope.',
      next: 'Fix the token.',
      status: 400,
    });
  });

  it('sends the cookie on a request that carries no body', async () => {
    // GET and DELETE take the `init`-less path through `request`.
    const mock = stub({ ok: true, status: 204 });

    await api.delete('/reports/1');

    expect(firstInit(mock).credentials).toBe('include');
  });

  it('cannot be talked out of sending credentials', async () => {
    /*
     * `credentials` is set after the caller's `init` is spread, so a call that
     * passes 'omit' — by habit, or copied from an example — still sends the
     * session cookie. The alternative is a request that 401s on its first try
     * for a reason nothing in the call site suggests.
     */
    const mock = stub({ ok: true, status: 200, json: () => Promise.resolve({}) });

    await request('/thing', { credentials: 'omit' });

    expect(firstInit(mock).credentials).toBe('include');
  });

  it('turns a network failure into a readable error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('offline'))),
    );

    const error = await api.get('/thing').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(StockSyncApiError);
    expect((error as StockSyncApiError).code).toBe('network_unreachable');
  });
});

describe('an expired access token', () => {
  /*
   * The reported bug. The access cookie lives fifteen minutes, the refresh
   * cookie thirty days, and nothing ever spent the second one — so a tab left
   * open past the quarter hour answered "You're not signed in." to whatever the
   * user did next, and only a reload and a fresh sign-in cleared it.
   */

  it('renews the session and replays the request instead of failing', async () => {
    const { paths } = scripted([
      unauthorised(), // the real request, with a dead access cookie
      ok({ access_expires_at: IN_AN_HOUR() }), // /auth/refresh
      ok({ total_skus: 1641 }), // the same request again
    ]);

    await expect(api.get('/analytics/kpis')).resolves.toEqual({ total_skus: 1641 });

    expect(paths).toEqual(['/api/analytics/kpis', '/api/auth/refresh', '/api/analytics/kpis']);
  });

  it('sends the cookie on the renewal and on the replay', async () => {
    // If either of these dropped it, renewal could not work and the replay
    // would 401 again — the retry would look broken rather than absent.
    const { mock } = scripted([
      unauthorised(),
      ok({ access_expires_at: IN_AN_HOUR() }),
      ok({}),
    ]);

    await api.get('/analytics/kpis');

    for (const call of mock.mock.calls) {
      expect(call[1]?.credentials).toBe('include');
    }
  });

  it('replays a POST with its body intact', async () => {
    const { mock } = scripted([unauthorised(), ok({}), ok({ id: 7 })]);

    await api.post('/reports', { kind: 'inventory' });

    const replay = mock.mock.calls[2];
    expect(replay?.[1]?.body).toBe(JSON.stringify({ kind: 'inventory' }));
  });

  it('renews once for a burst of requests, not once each', async () => {
    /*
     * Refresh tokens rotate — presenting one invalidates it. A dashboard fires
     * several requests at once, so parallel renewals would mean one success and
     * the rest rejected, taking the session they just created down with them.
     */
    const { paths } = scripted([]);
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const path = String(input);
        paths.push(path);
        call += 1;
        const reply =
          path === '/api/auth/refresh'
            ? ok({ access_expires_at: IN_AN_HOUR() })
            : call <= 3
              ? unauthorised() // the first three are the burst
              : ok({});
        return Promise.resolve({
          ok: reply.status === 200,
          status: reply.status,
          json: () => Promise.resolve(reply.body ?? {}),
        } as Response);
      }),
    );

    await Promise.all([api.get('/a'), api.get('/b'), api.get('/c')]);

    expect(paths.filter((p) => p === '/api/auth/refresh')).toHaveLength(1);
  });

  it('gives up and reports the session lost when renewal fails', async () => {
    const lost = vi.fn();
    onSessionExpired(lost);
    scripted([unauthorised(), unauthorised()]); // request, then a refused refresh

    await expect(api.get('/analytics/kpis')).rejects.toBeInstanceOf(StockSyncApiError);

    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('reports the session lost when the replay is refused too', async () => {
    // A session revoked from another tab, or an account deactivated: the
    // renewal works and the request still will not go through.
    const lost = vi.fn();
    onSessionExpired(lost);
    scripted([unauthorised(), ok({ access_expires_at: IN_AN_HOUR() }), unauthorised()]);

    await expect(api.get('/analytics/kpis')).rejects.toMatchObject({ status: 401 });

    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('never retries a rejected sign-in', async () => {
    // 401 there is the answer to a wrong password, not a stale session.
    // Retrying would swallow the message the form has to show.
    const { paths } = scripted([unauthorised()]);

    await expect(
      api.post('/auth/login', { email: 'a@b.co', password: 'no' }),
    ).rejects.toThrow();

    expect(paths).toEqual(['/api/auth/login']);
  });

  it('never retries logout or the renewal itself', async () => {
    for (const path of ['/auth/logout', '/auth/refresh']) {
      const { paths } = scripted([unauthorised()]);
      await api.post(path).catch(() => undefined);
      expect(paths).toHaveLength(1);
    }
  });
});

describe('the session expiry', () => {
  it('is adopted from any response that carries one', async () => {
    const at = IN_AN_HOUR();
    scripted([ok({ access_expires_at: at })]);

    await api.get('/auth/me');

    expect(sessionExpiresAt()).toBe(Date.parse(at));
  });

  it('survives a response that carries none', async () => {
    // `/me` and `/me/preferences` send null; taking it would throw away a
    // schedule that is still perfectly good.
    const at = IN_AN_HOUR();
    scripted([ok({ access_expires_at: at }), ok({ access_expires_at: null })]);

    await api.get('/auth/me');
    await api.patch('/me/preferences', { theme: 'dark' });

    expect(sessionExpiresAt()).toBe(Date.parse(at));
  });

  it('is cleared when the session is lost', async () => {
    scripted([ok({ access_expires_at: IN_AN_HOUR() })]);
    await api.get('/auth/me');

    scripted([unauthorised(), unauthorised()]);
    await api.get('/analytics/kpis').catch(() => undefined);

    expect(sessionExpiresAt()).toBeNull();
  });

  it('reports a refused renewal as rejected', async () => {
    scripted([unauthorised()]);

    await expect(refreshSession()).resolves.toEqual({ kind: 'rejected' });
  });
});

describe('ensureSession', () => {
  /*
   * The file downloads navigate the browser to a protected URL rather than
   * fetching it. A fetch that 401s is retried; a navigation that 401s just
   * replaces the page with an error envelope, so those two places ask first.
   */

  it('does not renew a session with time left on it', async () => {
    scripted([ok({ access_expires_at: IN_AN_HOUR() })]);
    await api.get('/auth/me');

    const { paths } = scripted([ok({})]);
    await expect(ensureSession()).resolves.toBe(true);

    expect(paths).toEqual([]);
  });

  it('renews one that is about to expire', async () => {
    scripted([ok({ access_expires_at: new Date(Date.now() + 5_000).toISOString() })]);
    await api.get('/auth/me');

    const { paths } = scripted([ok({ access_expires_at: IN_AN_HOUR() })]);
    await expect(ensureSession()).resolves.toBe(true);

    expect(paths).toEqual(['/api/auth/refresh']);
  });

  it('reports false when the session is over, so nothing navigates', async () => {
    scripted([ok({ access_expires_at: new Date(Date.now() - 1_000).toISOString() })]);
    await api.get('/auth/me');

    scripted([unauthorised()]);

    await expect(ensureSession()).resolves.toBe(false);
  });

  it('allows the navigation when no expiry is known', async () => {
    // An older server, or a response that carried none. Blocking downloads over
    // an absent field would be worse than letting the 401 path handle it.
    await expect(ensureSession()).resolves.toBe(true);
  });
});

describe('upload', () => {
  it('sends the file as multipart form data', async () => {
    const mock = stub({ ok: true, status: 200, json: () => Promise.resolve({}) });
    const file = new File(['SKU,Quantity\nDD-1,1\n'], 'stock.csv', { type: 'text/csv' });

    await api.upload('/imports/upload', file);

    const body = firstInit(mock).body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get('file')).toBe(file);
  });

  it('does not set Content-Type, so the browser can add the boundary', async () => {
    const mock = stub({ ok: true, status: 200, json: () => Promise.resolve({}) });

    await api.upload('/imports/upload', new File(['x'], 'stock.csv'));

    // Overriding it here strips the multipart boundary and the server cannot
    // parse the body.
    expect(firstHeaders(mock)['Content-Type']).toBeUndefined();
  });
});

describe('a renewal that could not be attempted', () => {
  /*
   * The reported bug. `/auth/refresh` has to write — it inserts a session row
   * and revokes the old one — and the rollup rebuild after a sync holds
   * SQLite's single write lock for 74 seconds on the live database against a
   * 10-second busy timeout. A renewal landing in that window came back 500.
   *
   * Every non-2xx was mapped to "session over", so a database that was merely
   * busy signed the user out moments after a sync that had worked perfectly.
   */

  const serverError = (): Reply => ({
    status: 500,
    body: {
      error: {
        code: 'internal_error',
        message: 'Something went wrong on our side.',
        next: 'Try again.',
      },
    },
  });

  it('is not a rejection', async () => {
    scripted([serverError()]);

    await expect(refreshSession()).resolves.toEqual({ kind: 'unavailable', status: 500 });
  });

  it('does not report the session lost', async () => {
    const lost = vi.fn();
    onSessionExpired(lost);
    scripted([unauthorised(), serverError()]);

    await api.get('/analytics/kpis').catch(() => undefined);

    expect(lost).not.toHaveBeenCalled();
  });

  it('says so through its own channel instead', async () => {
    const unavailable = vi.fn();
    onSessionUnavailable(unavailable);
    scripted([unauthorised(), serverError()]);

    await api.get('/analytics/kpis').catch(() => undefined);

    expect(unavailable).toHaveBeenCalledWith(500);
  });

  it('treats a dropped connection the same way', async () => {
    const lost = vi.fn();
    onSessionExpired(lost);
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => {
        call += 1;
        if (call === 1) {
          return Promise.resolve({
            ok: false,
            status: 401,
            json: () => Promise.resolve(unauthorised().body),
          } as Response);
        }
        return Promise.reject(new TypeError('offline'));
      }),
    );

    await api.get('/analytics/kpis').catch(() => undefined);

    expect(lost).not.toHaveBeenCalled();
  });

  it('still signs the user out when the server actually rejects the session', async () => {
    // The guard must not swallow a real expiry.
    const lost = vi.fn();
    onSessionExpired(lost);
    scripted([unauthorised(), unauthorised()]);

    await api.get('/analytics/kpis').catch(() => undefined);

    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('does not let a download proceed only when the session is rejected', async () => {
    scripted([ok({ access_expires_at: new Date(Date.now() - 1000).toISOString() })]);
    await api.get('/auth/me');

    scripted([serverError()]);
    // Busy is not refused: the cookie may well still be good.
    await expect(ensureSession()).resolves.toBe(true);

    scripted([unauthorised()]);
    await expect(ensureSession()).resolves.toBe(false);
  });
});

describe('the request log', () => {
  /*
   * "Why was I logged out just then" is asked immediately afterwards and about
   * the last handful of calls, so the log is in memory, capped, and readable
   * from the console.
   */

  it('records the url and status of every request', async () => {
    clearRequestLog();
    scripted([ok({})]);

    await api.get('/analytics/kpis');

    expect(requestLog().at(-1)).toMatchObject({
      url: '/analytics/kpis',
      status: 200,
      renewal: 'none',
      signedOut: false,
    });
  });

  it('records that a renewal was attempted and succeeded', async () => {
    clearRequestLog();
    scripted([unauthorised(), ok({ access_expires_at: IN_AN_HOUR() }), ok({})]);

    await api.get('/analytics/kpis');

    expect(requestLog().at(-1)).toMatchObject({ renewal: 'renewed', signedOut: false });
  });

  it('records an unavailable renewal without marking a sign-out', async () => {
    clearRequestLog();
    scripted([unauthorised(), { status: 500, body: {} }]);

    await api.get('/analytics/kpis').catch(() => undefined);

    const entries = requestLog();
    expect(entries.some((e) => e.url === '/auth/refresh' && e.renewal === 'unavailable')).toBe(
      true,
    );
    expect(entries.every((e) => !e.signedOut)).toBe(true);
  });

  it('marks the one request that actually signed the user out', async () => {
    clearRequestLog();
    scripted([unauthorised(), unauthorised()]);

    await api.get('/analytics/kpis').catch(() => undefined);

    expect(requestLog().at(-1)).toMatchObject({
      url: '/analytics/kpis',
      status: 401,
      renewal: 'rejected',
      signedOut: true,
    });
  });

  it('keeps only the recent history', async () => {
    clearRequestLog();
    scripted([ok({})]);

    for (let i = 0; i < 120; i += 1) await api.get(`/thing/${String(i)}`);

    expect(requestLog().length).toBe(100);
    expect(requestLog().at(-1)?.url).toBe('/thing/119');
  });
});
