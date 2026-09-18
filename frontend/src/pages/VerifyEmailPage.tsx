/**
 * Complete an email change from its verification link.
 *
 * Signed-out on purpose: the link is opened in whichever browser reads the new
 * mailbox, which is routinely not the one that asked. The token is the
 * authority, so this page needs no session — and must not require one, or the
 * change becomes impossible to finish on a phone.
 *
 * It posts on mount rather than behind a button. There is nothing for the
 * reader to decide: they asked for this from Settings, proved their password
 * there, and opening the link *is* the confirmation. A second "yes, really"
 * would be ceremony over a link that already took a deliberate click to reach.
 */

import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { AuthAside } from '../components/AuthAside';
import { Icon } from '../components/Icon';
import { StockSyncApiError, api } from '../lib/api';
import type { CurrentUser } from '../types/api';

type State =
  | { kind: 'working' }
  | { kind: 'done'; email: string }
  | { kind: 'failed'; message: string; next: string };

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';

  const [state, setState] = useState<State>({ kind: 'working' });
  // Effects run twice under StrictMode in development. The token is single
  // use, so a second post would report the first one's success as a failure.
  const posted = useRef(false);

  useEffect(() => {
    if (!token) {
      setState({
        kind: 'failed',
        message: 'This link is incomplete.',
        next: 'Open the link from your email again, or ask for a new one from Settings.',
      });
      return;
    }
    if (posted.current) return;
    posted.current = true;

    void (async () => {
      try {
        const me = await api.post<CurrentUser>('/auth/verify-email', { token });
        setState({ kind: 'done', email: me.email });
      } catch (caught) {
        setState(
          caught instanceof StockSyncApiError
            ? { kind: 'failed', message: caught.message, next: caught.next }
            : {
                kind: 'failed',
                message: 'Could not confirm that address.',
                next: 'Try the link again in a moment.',
              },
        );
      }
    })();
  }, [token]);

  return (
    <div id="login">
      <AuthAside />

      <div className="auth-main">
        <div className="auth-card">
          {state.kind === 'working' && (
            <>
              <h1 className="auth-h1">Confirming…</h1>
              <p className="auth-sub">One moment while we complete the change.</p>
            </>
          )}

          {state.kind === 'done' && (
            <>
              <div className="auth-ok" aria-hidden="true">
                <Icon name="check" size="l" />
              </div>
              <h1 className="auth-h1">Email updated</h1>
              <p className="auth-sub">
                You now sign in with <b>{state.email}</b>. Your password has not changed.
              </p>
              <Link className="btn pri blk" to="/login">
                Go to sign in
              </Link>
            </>
          )}

          {state.kind === 'failed' && (
            <>
              <h1 className="auth-h1">{state.message}</h1>
              <p className="auth-sub">{state.next}</p>
              <Link className="btn pri blk" to="/login">
                Back to sign in
              </Link>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
