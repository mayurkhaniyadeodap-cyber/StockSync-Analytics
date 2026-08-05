import { createContext, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import {
  RENEW_MARGIN_MS,
  StockSyncApiError,
  api,
  clearSessionExpiry,
  onSessionExpired,
  onSessionUnavailable,
  refreshSession,
  sessionExpiresAt,
} from '../lib/api';
import type { CurrentUser, PreferencesUpdate, ProfileUpdate } from '../types/api';

/**
 * `unreachable` is the fourth answer, and the reason this is not a boolean.
 *
 * "Signed out" and "could not tell" are different states and were being
 * collapsed into the first. A renewal that returns 500 — because the rollup
 * rebuild after a sync is holding SQLite's write lock and `/auth/refresh` has to
 * write — says nothing about whether the session is good, and answering it with
 * the login page threw away a session that was fine.
 */
export type AuthStatus = 'checking' | 'authenticated' | 'anonymous' | 'unreachable';

export interface AuthContextValue {
  status: AuthStatus;
  user: CurrentUser | null;
  login: (email: string, password: string, rememberMe: boolean) => Promise<void>;
  logout: () => Promise<void>;
  savePreferences: (patch: PreferencesUpdate) => Promise<void>;
  saveProfile: (patch: ProfileUpdate) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Never sit on a single timer longer than this.
 *
 * A machine that suspends, or a clock that jumps, can leave a long timer firing
 * far later than its wall-clock delay implied. Waking every ten minutes to look
 * at the clock costs nothing — `tick` reschedules without renewing when there
 * is still time left.
 */
const MAX_TIMER_MS = 10 * 60_000;

/**
 * How long to wait before trying a renewal that could not be attempted.
 *
 * Long enough to outlast the thing that blocked it — the rollup rebuild holds
 * SQLite's write lock for over a minute on the live database — and short enough
 * that the token has not expired by the next attempt.
 */
const RETRY_RENEWAL_MS = 30_000;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>('checking');

  const forget = useCallback(() => {
    setUser(null);
    setStatus('anonymous');
    clearSessionExpiry();
  }, []);

  // The backend confirmed the session is invalid — a 401 on the renewal itself.
  // Dropping to 'anonymous' is what sends the router to the login screen.
  useEffect(() => onSessionExpired(forget), [forget]);

  // The renewal could not be attempted: a 500, a dropped connection, a database
  // too busy to write. **Not a sign-out.** A user already signed in stays signed
  // in and the failed request simply fails; only a boot that never established a
  // user has nothing to keep, and that says "unreachable" rather than pretending
  // to know they are logged out.
  useEffect(
    () =>
      onSessionUnavailable(() => {
        setStatus((current) => (current === 'checking' ? 'unreachable' : current));
      }),
    [],
  );

  // On boot the session may already exist in an httpOnly cookie the page cannot
  // read, so the only way to know is to ask. Until it answers, status is
  // 'checking' and the router renders neither the app nor the login screen —
  // otherwise a reload would flash the login card at an already-signed-in user.
  //
  // A 401 here does not mean anonymous: `api` renews the session and replays
  // this call first. That is what makes reopening the app after lunch land on
  // the dashboard rather than on a login form the refresh cookie could have
  // answered for.
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const me = await api.get<CurrentUser>('/auth/me');
        if (!cancelled) {
          setUser(me);
          setStatus('authenticated');
        }
      } catch {
        // `onSessionUnavailable` may already have moved us to 'unreachable'.
        // Only a confirmed rejection becomes 'anonymous'; anything else leaves
        // the boot state alone so the router offers a retry, not a login form.
        if (!cancelled) {
          setStatus((current) => {
            if (current === 'unreachable') return current;
            clearSessionExpiry();
            return 'anonymous';
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [forget]);

  // Renew ahead of expiry, for as long as the tab is open.
  //
  // Reactive recovery alone would work — `api` catches the 401 and replays —
  // but it spends a failed round trip to learn something the server already
  // told us, and a plain browser navigation gets no second chance at all.
  useEffect(() => {
    if (status !== 'authenticated') return;

    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      if (cancelled) return;

      // The cap below can wake us long before there is anything to do — with a
      // fifteen-minute token the first wake is at ten. Renewing then would
      // rotate the session on a schedule of its own rather than the token's.
      const at = sessionExpiresAt();
      if (at !== null && Date.now() < at - RENEW_MARGIN_MS) {
        schedule();
        return;
      }

      const outcome = await refreshSession();
      if (cancelled) return;
      if (outcome.kind === 'renewed') {
        setUser(outcome.user);
        schedule();
      } else if (outcome.kind === 'rejected') {
        forget();
      } else {
        // Could not ask. Stay signed in and try again shortly rather than
        // treating a busy server as the end of the session.
        timer = window.setTimeout(() => void tick(), RETRY_RENEWAL_MS);
      }
    };

    const schedule = () => {
      if (cancelled) return;
      const at = sessionExpiresAt();
      // Unknown expiry — an older server, or a response that carries none.
      // Reactive recovery still covers it.
      if (at === null) return;
      const delay = Math.min(Math.max(at - Date.now() - RENEW_MARGIN_MS, 0), MAX_TIMER_MS);
      timer = window.setTimeout(() => void tick(), delay);
    };

    /**
     * A background tab's timers are throttled and a sleeping machine's stop
     * altogether, so a laptop opened an hour later wakes with a dead token and
     * a timer that still thinks it has minutes to go. Checking the clock on the
     * way back in is what makes reopening the application work.
     */
    const onWake = () => {
      if (cancelled || document.visibilityState !== 'visible') return;
      const at = sessionExpiresAt();
      if (at !== null && Date.now() >= at - RENEW_MARGIN_MS) {
        window.clearTimeout(timer);
        void tick();
      }
    };

    schedule();
    document.addEventListener('visibilitychange', onWake);
    window.addEventListener('online', onWake);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onWake);
      window.removeEventListener('online', onWake);
    };
  }, [status, forget]);

  const login = useCallback(async (email: string, password: string, rememberMe: boolean) => {
    const me = await api.post<CurrentUser>('/auth/login', {
      email,
      password,
      remember_me: rememberMe,
    });
    setUser(me);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post<void>('/auth/logout');
    } catch (error) {
      // A failed logout call must not strand the user in a signed-in shell.
      // Clearing locally is the safe direction to fail.
      if (!(error instanceof StockSyncApiError)) throw error;
    } finally {
      forget();
    }
  }, [forget]);

  const savePreferences = useCallback(async (patch: PreferencesUpdate) => {
    const updated = await api.patch<CurrentUser>('/me/preferences', patch);
    setUser(updated);
  }, []);

  // The response is the whole user, so the header avatar and initials follow a
  // rename without a second request or a reload.
  const saveProfile = useCallback(async (patch: ProfileUpdate) => {
    setUser(await api.patch<CurrentUser>('/me', patch));
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, login, logout, savePreferences, saveProfile }),
    [status, user, login, logout, savePreferences, saveProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
