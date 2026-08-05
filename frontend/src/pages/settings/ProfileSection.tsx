/**
 * Settings → Profile.
 *
 * Name and time zone are yours to change. Email is the login identity, so
 * changing it is an authentication change rather than a profile edit; role is
 * a job title the workspace admin sets. Both are shown — you should be able to
 * see what the system thinks you are — and neither is writable here, which the
 * fields say rather than leaving you to discover by trying.
 */

import { useEffect, useMemo, useState } from 'react';

import { Icon } from '../../components/Icon';
import { useAuth } from '../../hooks/useAuth';
import { useToast } from '../../hooks/useToast';
import { StockSyncApiError } from '../../lib/api';

/**
 * Real IANA zone names from the platform, so the list is neither invented nor
 * frozen at the moment this was written. Older engines that lack it fall back
 * to the zones this workspace plausibly spans, plus whatever is already saved —
 * a list that cannot show the current value would silently change it on save.
 */
function timezones(current: string): string[] {
  const supported = Intl.supportedValuesOf?.('timeZone') ?? [];
  const names = supported.length
    ? supported
    : ['Asia/Kolkata', 'Asia/Dubai', 'Europe/London', 'America/New_York', 'UTC'];
  return names.includes(current) ? names : [current, ...names];
}

export function ProfileSection() {
  const { user, saveProfile } = useAuth();
  const { toast } = useToast();

  const [fullName, setFullName] = useState('');
  const [timezone, setTimezone] = useState('');
  const [saving, setSaving] = useState(false);

  // Seeded from the server rather than initialised once, so the form follows a
  // change made in another tab instead of overwriting it on the next save.
  useEffect(() => {
    if (!user) return;
    setFullName(user.full_name);
    setTimezone(user.timezone);
  }, [user]);

  const zones = useMemo(() => timezones(user?.timezone ?? 'UTC'), [user?.timezone]);

  if (!user) return null;

  const trimmed = fullName.trim();
  const dirty = trimmed !== user.full_name || timezone !== user.timezone;

  async function save() {
    setSaving(true);
    try {
      await saveProfile({ full_name: trimmed, timezone });
      toast('Profile saved', 'moss');
    } catch (caught) {
      toast(
        caught instanceof StockSyncApiError ? caught.message : "Couldn't save your profile.",
        'rust',
        true,
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel">
      <div className="p-hd">
        <h3>Profile</h3>
        <div className="r">
          <span className="avatar">{user.initials}</span>
        </div>
      </div>

      <div className="p-bd">
        <div className="field">
          <label htmlFor="profile-name">
            Full name <span className="req">*</span>
          </label>
          <input
            id="profile-name"
            className="inp"
            value={fullName}
            maxLength={120}
            onChange={(event) => setFullName(event.target.value)}
          />
          <div className="help">Shown in the header and on anything you export.</div>
        </div>

        <div className="field">
          <label htmlFor="profile-email">Email</label>
          <input id="profile-email" className="inp" value={user.email} disabled readOnly />
          <div className="help">
            This is how you sign in. Your workspace admin can change it.
          </div>
        </div>

        <div className="field">
          <label htmlFor="profile-role">Role</label>
          <input id="profile-role" className="inp" value={user.role} disabled readOnly />
          <div className="help">Roles are managed by your workspace admin.</div>
        </div>

        <div className="field">
          <label htmlFor="profile-tz">Time zone</label>
          <select
            id="profile-tz"
            className="inp"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
          >
            {zones.map((zone) => (
              <option key={zone} value={zone}>
                {zone.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
          <div className="help">Dates and times are shown in this zone.</div>
        </div>
      </div>

      <div className="p-ft">
        <span className="hint">
          <Icon name="user" size="s" /> {user.workspace.name}
        </span>
        <span className="spacer" />
        <button
          className="btn cta"
          onClick={() => void save()}
          disabled={!dirty || !trimmed || saving}
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}
