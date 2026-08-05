import { useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { Icon } from '../components/Icon';
import { useAuth } from '../hooks/useAuth';
import { StockSyncApiError } from '../lib/api';

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Design doc §6: "Card fades out (180ms), Dashboard fades in." */
const FADE_MS = 180;

/**
 * Design doc §6.
 *
 * Deliberately calm: a centred single card, no marketing imagery, because this
 * is an internal operational tool rather than a consumer sign-up funnel.
 */
export function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  // Off on every render of this page: revealing a password is a per-attempt
  // decision, never a setting that persists into the next sign-in.
  const [showPassword, setShowPassword] = useState(false);

  const [emailError, setEmailError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Held between a successful login and the navigation, so the card can fade.
  const [leaving, setLeaving] = useState(false);

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from ?? '/dashboard';
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();

    // Client-side format check only. Field borders turn Rust for a format
    // error; a rejected credential is a banner, never a field highlight (§6).
    if (!EMAIL_PATTERN.test(email.trim())) {
      setEmailError('Enter a valid email address.');
      return;
    }
    setEmailError(null);
    setBanner(null);
    setSubmitting(true);

    try {
      await login(email.trim(), password, rememberMe);
      const from = (location.state as { from?: string } | null)?.from ?? '/dashboard';

      // The fade is decoration, not a step: when motion is suppressed the CSS
      // transition is already neutered, so waiting on it would just be a stall.
      setLeaving(true);
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      window.setTimeout(() => navigate(from, { replace: true }), reduced ? 0 : FADE_MS);
    } catch (error) {
      setBanner(
        error instanceof StockSyncApiError ? error.message : 'Incorrect email or password.',
      );
      setSubmitting(false);
    }
  }

  return (
    <div id="login" className={leaving ? 'gone' : undefined}>
      <div>
        <div className="login-card">
          {/* The strata motif: four stacked layers, the product's core visual idea. */}
          <div className="login-strata">
            <i style={{ background: 'var(--slate)' }} />
            <i style={{ background: 'var(--moss)' }} />
            <i style={{ background: 'var(--amber)' }} />
            <i style={{ background: 'var(--clay)' }} />
          </div>

          <form className="login-bd" onSubmit={(e) => void handleSubmit(e)} noValidate>
            <div className="login-mark">
              <Icon name="layers" size="l" style={{ color: 'var(--slate)' }} />
              <div>
                <b style={{ fontSize: 19, letterSpacing: '.02em' }}>StockSync Analytics</b>
              </div>
            </div>
            <p className="login-sub">Inventory &amp; Shopify sales reconciliation</p>

            {banner && (
              <div className="banner err" role="alert">
                <Icon name="warn" size="s" style={{ marginTop: 2 }} />
                <span>{banner}</span>
              </div>
            )}

            <div className="field">
              <label htmlFor="login-email">Email</label>
              <input
                id="login-email"
                className={`inp${emailError ? ' bad' : ''}`}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                // Validation on blur, not per keystroke — design doc §14.
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

            <div className="field">
              <label htmlFor="login-password">Password</label>
              <div className="inp-wrap">
                <input
                  id="login-password"
                  className="inp"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  disabled={submitting}
                />
                {/* type="button" or it submits the form. A real button, so Enter
                    and Space work without a keydown handler of our own. */}
                <button
                  type="button"
                  className="inp-act"
                  onClick={() => setShowPassword((shown) => !shown)}
                  disabled={submitting}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                  aria-controls="login-password"
                >
                  <Icon name={showPassword ? 'eye-off' : 'eye'} size="s" />
                </button>
              </div>
            </div>

            <label className="check" style={{ marginBottom: 20 }}>
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                disabled={submitting}
              />{' '}
              Keep me signed in
            </label>

            {/* Height comes from .login-bd .btn.blk, not an inline style, so it
                stays in step with the field height next to it. */}
            <button className="btn pri blk" type="submit" disabled={submitting}>
              {submitting ? (
                <>
                  {/* currentColor, not the prototype's #fff: .btn.pri flips its
                      text to near-black in dark mode, and a white dot on it
                      would be the one thing on this card that ignores theme. */}
                  <span className="dot slate" style={{ background: 'currentColor' }} /> Signing
                  in…
                </>
              ) : (
                'Log in'
              )}
            </button>
          </form>

          <div className="login-foot">
            <span>StockSync Analytics · Deodap</span>
          </div>
        </div>
      </div>
    </div>
  );
}
