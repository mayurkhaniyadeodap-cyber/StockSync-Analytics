/**
 * Changing your password.
 *
 * Kept out of the Profile panel deliberately. Name and time zone save together
 * on one button and are harmless if you wander off mid-edit; this one requires
 * the current password and ends every other session. Putting them behind one
 * "Save changes" would make a password change something you could do by
 * accident while fixing a typo in your name.
 *
 * A `Sign-in email` panel lived here too and was taken off the page. The
 * endpoints it called are still there, so restoring it is a matter of writing
 * the component again rather than rebuilding the flow behind it.
 *
 * **The panel repeats no rule the server owns.** Length and whether the current
 * password is right are decided there and those refusals are rendered verbatim.
 * What is checked here is only what the server has no opinion about: that the
 * two new-password fields match, which is a typing mistake rather than a
 * security decision.
 */

import { useState } from 'react';
import type { FormEvent, ReactNode } from 'react';

import { Icon } from '../../components/Icon';
import { useAuth } from '../../hooks/useAuth';
import { useToast } from '../../hooks/useToast';
import { StockSyncApiError, api } from '../../lib/api';
import type { CurrentUser } from '../../types/api';

interface Failure {
  message: string;
  next: string;
}

function failure(caught: unknown, fallback: string): Failure {
  return caught instanceof StockSyncApiError
    ? { message: caught.message, next: caught.next }
    : { message: fallback, next: 'Try again in a moment.' };
}

/** The server's refusal, shown as it was written. */
function Banner({ error }: { error: Failure | null }) {
  if (!error) return null;
  return (
    <div className="banner err" role="alert">
      <Icon name="warn" size="s" style={{ marginTop: 2 }} />
      <span>
        <b>{error.message}</b> {error.next}
      </span>
    </div>
  );
}

/**
 * A password input with its own reveal toggle.
 *
 * `type="button"`, or it submits the form it sits in. A real button, so Enter
 * and Space work without a key handler of our own.
 */
function SecretField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  disabled,
  help,
  invalid,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (next: string) => void;
  autoComplete: string;
  disabled: boolean;
  help?: ReactNode;
  invalid?: boolean;
}) {
  const [shown, setShown] = useState(false);

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="inp-wrap">
        <input
          id={id}
          className={`inp${invalid ? ' bad' : ''}`}
          type={shown ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          disabled={disabled}
        />
        <button
          type="button"
          className="inp-act"
          onClick={() => setShown((was) => !was)}
          disabled={disabled}
          aria-label={shown ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          aria-pressed={shown}
          aria-controls={id}
        >
          <Icon name={shown ? 'eye-off' : 'eye'} size="s" />
        </button>
      </div>
      {help ? <div className="help">{help}</div> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------

export function PasswordSection() {
  const { toast } = useToast();
  const { adopt } = useAuth();

  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [mismatch, setMismatch] = useState(false);
  const [error, setError] = useState<Failure | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();

    if (next !== confirm) {
      setMismatch(true);
      return;
    }

    setMismatch(false);
    setError(null);
    setSaving(true);
    try {
      // The server revokes every session and hands this browser a replacement
      // on the way out, so the response is the signed-in user and there is
      // nothing left to fetch.
      adopt(
        await api.post<CurrentUser>('/auth/change-password', {
          current_password: current,
          new_password: next,
        }),
      );
      setCurrent('');
      setNext('');
      setConfirm('');
      toast('Password changed. Other devices have been signed out.', 'moss');
    } catch (caught) {
      setError(failure(caught, 'Could not change your password.'));
    } finally {
      setSaving(false);
    }
  }

  const ready = current !== '' && next !== '' && confirm !== '';

  return (
    <div className="panel">
      <div className="p-hd">
        <span className="p-chip amber" aria-hidden="true">
          <Icon name="warn" size="s" />
        </span>
        <h3>Change password</h3>
        <span className="hint">
          Signing in elsewhere will need the new password — every other session ends.
        </span>
      </div>

      <div className="p-bd">
        <form onSubmit={(e) => void submit(e)} noValidate>
          <Banner error={error} />

          <SecretField
            id="pw-current"
            label="Current password"
            value={current}
            onChange={setCurrent}
            autoComplete="current-password"
            disabled={saving}
          />

          <SecretField
            id="pw-new"
            label="New password"
            value={next}
            onChange={setNext}
            autoComplete="new-password"
            disabled={saving}
            help="At least 12 characters."
          />

          <SecretField
            id="pw-confirm"
            label="Confirm new password"
            value={confirm}
            onChange={setConfirm}
            autoComplete="new-password"
            disabled={saving}
            invalid={mismatch}
          />
          {mismatch && (
            <div className="err-msg" style={{ marginTop: -8, marginBottom: 12 }}>
              <Icon name="warn" size="s" /> The two passwords do not match.
            </div>
          )}

          <div className="acts">
            <button className="btn cta" type="submit" disabled={saving || !ready}>
              {saving ? 'Changing…' : 'Change password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
