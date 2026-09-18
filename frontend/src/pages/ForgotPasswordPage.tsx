/**
 * Ask for a reset link.
 *
 * **The success state does not say whether the address exists.** The server
 * answers identically either way — an endpoint that distinguished them would be
 * an account-enumeration oracle on a public page — so this screen says what the
 * server said and no more. It would be easy to write "Check your inbox!" here
 * and undo that on the client, which is why the copy is the sentence the API
 * returned rather than one invented for the occasion.
 */

import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { AuthAside } from '../components/AuthAside';
import { Icon } from '../components/Icon';
import { StockSyncApiError, api } from '../lib/api';

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

interface Accepted {
  detail: string;
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [emailError, setEmailError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = email.trim();

    if (!EMAIL_PATTERN.test(trimmed)) {
      setEmailError('Enter a valid email address.');
      return;
    }

    setEmailError(null);
    setBanner(null);
    setSubmitting(true);
    try {
      const body = await api.post<Accepted>('/auth/forgot-password', { email: trimmed });
      setSent(body.detail);
    } catch (caught) {
      setBanner(
        caught instanceof StockSyncApiError
          ? caught.message
          : 'Could not send a reset link just now.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div id="login">
      <AuthAside />

      <div className="auth-main">
        <div className="auth-card">
          {sent ? (
            <>
              <div className="auth-ok" aria-hidden="true">
                <Icon name="check" size="l" />
              </div>
              <h1 className="auth-h1">Check your email</h1>
              {/* The server's own sentence, which is deliberately non-committal
                  about whether the address had an account. */}
              <p className="auth-sub">{sent}</p>
              <Link className="btn pri blk" to="/login">
                Back to sign in
              </Link>
            </>
          ) : (
            <form onSubmit={(e) => void handleSubmit(e)} noValidate>
              <h1 className="auth-h1">Forgot your password?</h1>
              <p className="auth-sub">
                Enter the address you sign in with and we&rsquo;ll send a link to set a new
                password.
              </p>

              {banner && (
                <div className="banner err" role="alert">
                  <Icon name="warn" size="s" style={{ marginTop: 2 }} />
                  <span>{banner}</span>
                </div>
              )}

              <div className="field">
                <label htmlFor="forgot-email">Email address</label>
                <input
                  id="forgot-email"
                  className={`inp${emailError ? ' bad' : ''}`}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onBlur={() =>
                    setEmailError(
                      email && !EMAIL_PATTERN.test(email.trim())
                        ? 'Enter a valid email address.'
                        : null,
                    )
                  }
                  autoComplete="username"
                  disabled={submitting}
                  autoFocus
                />
                {emailError && (
                  <div className="err-msg">
                    <Icon name="warn" size="s" /> {emailError}
                  </div>
                )}
              </div>

              <button className="btn pri blk" type="submit" disabled={submitting}>
                {submitting ? 'Sending…' : 'Send reset link'}
              </button>

              <p className="auth-alt">
                Remembered it? <Link to="/login">Back to sign in</Link>
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
