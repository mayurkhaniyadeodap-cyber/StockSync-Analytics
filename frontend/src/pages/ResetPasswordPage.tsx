/**
 * Set a new password from a reset link.
 *
 * The token arrives in the query string, which is where an emailed link can
 * carry it. It is read once into state and the form posts it back; nothing
 * renders it, because a token on screen is a token in a screenshot.
 *
 * **Confirmation is checked here and only here.** The server takes one password
 * and has no opinion about a second field — a mismatch is a typing mistake, not
 * a security decision, and sending both would mean two places could disagree
 * about what "they match" means. What the server does own is the length floor,
 * and its refusal is shown verbatim rather than duplicated as a client rule
 * that could drift from it.
 */

import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { AuthAside } from '../components/AuthAside';
import { Icon } from '../components/Icon';
import { useAuth } from '../hooks/useAuth';
import { StockSyncApiError, api } from '../lib/api';
import type { CurrentUser } from '../types/api';

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { adopt } = useAuth();

  const token = params.get('token') ?? '';

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();

    if (password !== confirm) {
      setFieldError('The two passwords do not match.');
      return;
    }

    setFieldError(null);
    setBanner(null);
    setNext(null);
    setSubmitting(true);
    try {
      // The server signs the account in as part of the reset: the cookies are
      // set and the user comes back on the response, so there is nothing left
      // to fetch — the provider just has to be told.
      adopt(await api.post<CurrentUser>('/auth/reset-password', { token, password }));
      void navigate('/dashboard', { replace: true });
    } catch (caught) {
      if (caught instanceof StockSyncApiError) {
        setBanner(caught.message);
        setNext(caught.next);
      } else {
        setBanner('Could not set your password just now.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  // A link with no token at all: nothing to verify, and the form would only
  // fail on submit. Saying so now saves the round trip and the typing.
  if (!token) {
    return (
      <div id="login">
        <AuthAside />
        <div className="auth-main">
          <div className="auth-card">
            <h1 className="auth-h1">This link is incomplete</h1>
            <p className="auth-sub">
              The address is missing its reset token. Open the link from your email again, or
              ask for a new one.
            </p>
            <Link className="btn pri blk" to="/forgot-password">
              Request a new link
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div id="login">
      <AuthAside />

      <div className="auth-main">
        <div className="auth-card">
          <form onSubmit={(e) => void handleSubmit(e)} noValidate>
            <h1 className="auth-h1">Set a new password</h1>
            <p className="auth-sub">
              Choose something you have not used here before. Signing in elsewhere will need the
              new password.
            </p>

            {banner && (
              <div className="banner err" role="alert">
                <Icon name="warn" size="s" style={{ marginTop: 2 }} />
                <span>
                  <b>{banner}</b> {next}
                </span>
              </div>
            )}

            <div className="field">
              <label htmlFor="reset-password">New password</label>
              <div className="inp-wrap">
                <input
                  id="reset-password"
                  className={`inp${fieldError ? ' bad' : ''}`}
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  disabled={submitting}
                  autoFocus
                />
                <button
                  type="button"
                  className="inp-act"
                  onClick={() => setShowPassword((shown) => !shown)}
                  disabled={submitting}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                  aria-controls="reset-password"
                >
                  <Icon name={showPassword ? 'eye-off' : 'eye'} size="s" />
                </button>
              </div>
              <div className="hint">At least 12 characters.</div>
            </div>

            <div className="field">
              <label htmlFor="reset-confirm">Confirm new password</label>
              <input
                id="reset-confirm"
                className={`inp${fieldError ? ' bad' : ''}`}
                type={showPassword ? 'text' : 'password'}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                onBlur={() =>
                  setFieldError(
                    confirm && password !== confirm ? 'The two passwords do not match.' : null,
                  )
                }
                autoComplete="new-password"
                disabled={submitting}
              />
              {fieldError && (
                <div className="err-msg">
                  <Icon name="warn" size="s" /> {fieldError}
                </div>
              )}
            </div>

            <button className="btn pri blk" type="submit" disabled={submitting}>
              {submitting ? 'Setting…' : 'Set password and sign in'}
            </button>

            <p className="auth-alt">
              <Link to="/login">Back to sign in</Link>
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
