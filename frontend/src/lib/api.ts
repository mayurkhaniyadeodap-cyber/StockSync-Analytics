/**
 * API client.
 *
 * The auth JWT rides in an httpOnly cookie from M1, so every request sends
 * credentials and none of them touch localStorage. There is no Authorization
 * header to attach and no token for the page to load first — the browser sends
 * the cookie or it does not, which is why nothing here can be "not ready yet".
 *
 * **The access cookie lives fifteen minutes; the refresh cookie lives thirty
 * days.** For a long time nothing renewed the first one, so any tab left open
 * past the quarter hour got "You're not signed in." on its next request — a
 * background poll, a filter change, a report — while the session behind it was
 * still perfectly valid. That is the intermittency: it depended entirely on how
 * long the tab had been sitting there.
 *
 * So a 401 on a protected path is treated as "renew and try again" rather than
 * as an answer. Only if the renewal itself fails is the session really over,
 * and then `onSessionExpired` fires so the router can send the user to sign in
 * instead of leaving a shell full of failed panels.
 */

import type { ApiError, CurrentUser, ErrorEnvelope } from '../types/api';

/**
 * Exported because a file download cannot go through `request()`: the browser has
 * to navigate to the URL so it handles Content-Disposition and the filename, and
 * that needs the same prefix every fetch uses.
 */
export const API_BASE = '/api';

const BASE = API_BASE;

/**
 * An error the UI can render directly.
 *
 * Design doc §16: an error states what happened and what to do next. The server
 * supplies both, so screens never have to invent a recovery instruction.
 */
export class StockSyncApiError extends Error {
  readonly code: string;
  readonly next: string;
  readonly status: number;
  readonly detail?: Record<string, unknown>;

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.name = 'StockSyncApiError';
    this.status = status;
    this.code = error.code;
    this.next = error.next;
    this.detail = error.detail;
  }
}

const NETWORK_ERROR: ApiError = {
  code: 'network_unreachable',
  message: "StockSync Analytics couldn't reach the server.",
  next: 'Check your connection and try again.',
};

/**
 * The endpoints a 401 must never be retried on.
 *
 * `/auth/login` answers 401 for a wrong password, which is the response, not a
 * stale session — retrying it would swallow the error the form has to show.
 * The other two are how a session is ended or renewed; retrying either would
 * recurse.
 */
const NO_RETRY = new Set(['/auth/login', '/auth/logout', '/auth/refresh']);

/** One line per request, for diagnosing a session that ended unexpectedly. */
export interface RequestRecord {
  url: string;
  status: number;
  /** Whether a renewal was attempted, and what came back. */
  renewal: Renewal['kind'] | 'none';
  /** True only when the user was actually signed out by this request. */
  signedOut: boolean;
  at: string;
}

/**
 * The last requests this tab made, newest last.
 *
 * Kept in memory and capped, because the question it answers — "why did I get
 * logged out just then" — is asked immediately afterwards and about the last
 * handful of calls. Read it from the console with `stocksyncRequestLog()`.
 */
const REQUEST_LOG: RequestRecord[] = [];
const LOG_LIMIT = 100;

function log(
  url: string,
  status: number,
  over: { renewal?: RequestRecord['renewal']; signedOut?: boolean } = {},
): void {
  REQUEST_LOG.push({
    url,
    status,
    renewal: over.renewal ?? 'none',
    signedOut: over.signedOut ?? false,
    at: new Date().toISOString(),
  });
  if (REQUEST_LOG.length > LOG_LIMIT) REQUEST_LOG.shift();
}

export function requestLog(): readonly RequestRecord[] {
  return REQUEST_LOG;
}

export function clearRequestLog(): void {
  REQUEST_LOG.length = 0;
}

/**
 * What came back from trying to renew.
 *
 * The distinction is the whole point. A renewal that is **rejected** means the
 * session is genuinely over and the user has to sign in again. A renewal that is
 * **unavailable** — 500, 503, a dropped connection, a database too busy to write
 * — means we could not ask right now, which says nothing about the session.
 *
 * Collapsing the two logged people out for the wrong reason. `/auth/refresh`
 * writes (it inserts a session row and revokes the old one), and the rollup
 * rebuild after a sync holds SQLite's single write lock for 74 seconds on the
 * live database against a 10-second busy timeout. A renewal landing in that
 * window returned 500, which read as "signed out" and bounced the user to the
 * login page moments after a sync that had worked perfectly.
 */
export type Renewal =
  | { kind: 'renewed'; user: CurrentUser }
  | { kind: 'rejected' }
  | { kind: 'unavailable'; status: number };

let renewal: Promise<Renewal> | null = null;
let sessionLost: () => void = () => {};
let sessionUnavailable: (status: number) => void = () => {};

/**
 * When the access cookie stops being accepted, as epoch ms, or null if unknown.
 *
 * Lives here rather than in `AuthProvider` because this is the module that acts
 * on it, and because the two file downloads need it without wanting a React
 * context — a page that only exports a CSV should not have to be mounted inside
 * an auth provider to do it.
 */
let expiresAt: number | null = null;

/**
 * Renew this long before the token actually dies.
 *
 * Wide enough for a slow round trip and a clock a minute out of step with the
 * server's. Renewing early costs a request; renewing late costs a failed one.
 */
export const RENEW_MARGIN_MS = 60_000;

/** Adopt the expiry from any response that carries one. */
function noteExpiry(body: unknown): void {
  const at = (body as { access_expires_at?: string | null } | null)?.access_expires_at;
  // Only a real value counts. `/me` and `/me/preferences` send null because
  // they neither issue a token nor read one, and taking their null would throw
  // away a schedule that is still good.
  if (at) expiresAt = Date.parse(at);
}

/** What the client believes about the current token. Null means "not told". */
export function sessionExpiresAt(): number | null {
  return expiresAt;
}

export function clearSessionExpiry(): void {
  expiresAt = null;
}

/**
 * Guarantee a live access cookie, renewing only if one is close to expiring.
 *
 * For the file downloads, which navigate the browser to a protected URL instead
 * of going through `request`. A fetch that 401s is retried; a navigation that
 * 401s just replaces the page with an error envelope.
 */
export async function ensureSession(): Promise<boolean> {
  if (expiresAt === null) return true; // Never told; the 401 path still covers it.
  if (Date.now() < expiresAt - RENEW_MARGIN_MS) return true;
  const outcome = await refreshSession();
  // 'unavailable' is not a refusal. Let the navigation go: the cookie may well
  // still be good, and blocking a download because the server was busy renewing
  // would be the same mistake in a smaller place.
  return outcome.kind !== 'rejected';
}

/**
 * Register what happens when the session cannot be renewed.
 *
 * One handler, set by `AuthProvider`, so a genuinely expired session lands the
 * user on the login screen rather than in a shell where every panel quietly
 * says it could not load.
 */
export function onSessionExpired(handler: () => void): () => void {
  sessionLost = handler;
  return () => {
    sessionLost = () => {};
  };
}

/**
 * Register what happens when a renewal could not be attempted.
 *
 * Deliberately separate from `onSessionExpired`: this is not a sign-out, and a
 * provider that treated it as one would be the bug this exists to prevent.
 */
export function onSessionUnavailable(handler: (status: number) => void): () => void {
  sessionUnavailable = handler;
  return () => {
    sessionUnavailable = () => {};
  };
}

/**
 * Rotate the session. Resolves to the user on success, null when it is over.
 *
 * **Single-flight, and it has to be.** Refresh tokens rotate: presenting one
 * invalidates it. A dashboard load fires half a dozen requests at once, so six
 * parallel refreshes would mean one success and five rejections — and the five
 * would take the just-created session down with them. Everything that needs a
 * renewal while one is in flight waits on the same promise instead.
 */
export function refreshSession(): Promise<Renewal> {
  renewal ??= send('/auth/refresh', { method: 'POST' })
    .then(async (response): Promise<Renewal> => {
      if (response.ok) {
        const user = (await response.json()) as CurrentUser;
        noteExpiry(user);
        return { kind: 'renewed', user };
      }
      // 401 is the server saying this refresh token is no longer good — the one
      // answer that means signed out. Every other status is the server, or the
      // network, being unable to answer.
      if (response.status === 401) {
        clearSessionExpiry();
        return { kind: 'rejected' };
      }
      log('/auth/refresh', response.status, { renewal: 'unavailable' });
      return { kind: 'unavailable', status: response.status };
    })
    .catch((): Renewal => {
      log('/auth/refresh', 0, { renewal: 'unavailable' });
      return { kind: 'unavailable', status: 0 };
    })
    .finally(() => {
      renewal = null;
    });
  return renewal;
}

async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${BASE}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        // FormData must set its own Content-Type: the multipart boundary is
        // generated by the browser, and overriding it here makes the body
        // unparseable on the server.
        ...(init.body && !(init.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...init.headers,
      },
    });
  } catch {
    // fetch only rejects on network failure, never on a 4xx/5xx.
    throw new StockSyncApiError(0, NETWORK_ERROR);
  }
}

async function unwrap<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const envelope = body as ErrorEnvelope | null;
    throw new StockSyncApiError(
      response.status,
      envelope?.error ?? {
        code: 'request_failed',
        message: 'The request failed.',
        next: 'Try again.',
      },
    );
  }

  // Login and /auth/me carry the new token's expiry; everything else carries
  // nothing and leaves the current schedule alone.
  noteExpiry(body);
  return body as T;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await send(path, init);
  let renewed: Renewal['kind'] | 'none' = 'none';

  if (response.status === 401 && !NO_RETRY.has(path)) {
    const outcome = await refreshSession();
    renewed = outcome.kind;

    if (outcome.kind === 'renewed') {
      // Replaying is safe: every body we send is a JSON string or a FormData,
      // both of which fetch can read twice. Nothing here streams a request.
      response = await send(path, init);
    } else if (outcome.kind === 'unavailable') {
      // We could not ask whether the session is still good, so we do not get to
      // conclude it is not. The request fails and the user stays signed in.
      log(path, response.status, { renewal: 'unavailable', signedOut: false });
      sessionUnavailable(outcome.status);
      return unwrap<T>(response);
    }

    // Rejected outright, or renewed and the replay refused anyway — a session
    // revoked from Settings, an account deactivated. Both really are signed out.
    if (response.status === 401) {
      clearSessionExpiry();
      log(path, 401, { renewal: renewed, signedOut: true });
      sessionLost();
      return unwrap<T>(response);
    }
  }

  log(path, response.status, { renewal: renewed });
  return unwrap<T>(response);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  /** multipart/form-data upload. Field name must match the FastAPI parameter. */
  upload: <T>(path: string, file: File, field = 'file') => {
    const form = new FormData();
    form.append(field, file);
    return request<T>(path, { method: 'POST', body: form });
  },
};
