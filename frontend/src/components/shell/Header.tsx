import { useCallback, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import type { ShopifyStatus } from '../../contexts/ShopifyStatusContext';
import { useAuth } from '../../hooks/useAuth';
import { useOnClickOutside } from '../../hooks/useOnClickOutside';
import { useShopifyStatus } from '../../hooks/useShopifyStatus';
import { useTheme } from '../../hooks/useTheme';
import { useToast } from '../../hooks/useToast';
import { freshness } from '../../lib/format';
import { Icon } from '../Icon';

export type SyncState = 'checking' | 'ok' | 'busy' | 'failed' | 'idle';

/** Design doc §3: dot colour + short label, nothing more. */
const SYNC_LABEL: Record<SyncState, { tone: string; label: string }> = {
  checking: { tone: '', label: 'Checking…' },
  ok: { tone: 'moss', label: 'Synced' },
  busy: { tone: 'slate', label: 'Syncing…' },
  failed: { tone: 'rust', label: 'Sync failed' },
  idle: { tone: 'amber', label: 'Not connected' },
};

interface HeaderProps {
  onOpenNav: () => void;
}

type OpenPopover = 'sync' | 'notifications' | 'user' | null;

export function Header({ onOpenNav }: HeaderProps) {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { toast } = useToast();
  const navigate = useNavigate();
  const status = useShopifyStatus();

  const [open, setOpen] = useState<OpenPopover>(null);
  const headerRight = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(null), []);
  useOnClickOutside(headerRight, close, open !== null);

  const sync = SYNC_LABEL[pillState(status)];
  const connected = status.connection?.connected === true;
  const lastSyncedAt = status.summary?.last_synced_at ?? null;

  const handleLogout = useCallback(async () => {
    close();
    await logout();
    navigate('/login', { replace: true });
  }, [close, logout, navigate]);

  return (
    <header className="hdr">
      <button className="icon-btn side-toggle" onClick={onOpenNav} aria-label="Open navigation">
        <Icon name="menu" />
      </button>

      <button
        className="mark"
        onClick={() => navigate('/dashboard')}
        aria-label="Go to dashboard"
      >
        <Icon name="layers" size="l" style={{ color: 'var(--slate)' }} />
        <div style={{ textAlign: 'left' }}>
          <b>StockSync</b>
          <small>{user?.workspace.name ?? 'Analytics'}</small>
        </div>
      </button>

      <div className="hdr-r" ref={headerRight}>
        <div style={{ position: 'relative' }}>
          <button
            className="syncpill"
            onClick={() => setOpen(open === 'sync' ? null : 'sync')}
            aria-expanded={open === 'sync'}
            aria-haspopup="menu"
          >
            <span className={`dot ${sync.tone}`} />
            <span className="sl">{sync.label}</span>
            <Icon name="down" size="s" style={{ opacity: 0.5 }} />
          </button>

          <div className={`pop${open === 'sync' ? ' on' : ''}`} role="menu">
            <div className="pop-hd">
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                {status.connection?.connection?.store_name ??
                  status.connection?.connection?.shop_domain ??
                  'Shopify sync'}
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-45)' }}>
                {!connected
                  ? 'No store connected yet'
                  : lastSyncedAt
                    ? `Synced ${freshness(new Date(lastSyncedAt))}`
                    : 'Never synced'}
              </div>
            </div>
            <button
              className="pop-item"
              role="menuitem"
              onClick={() => {
                close();
                navigate('/shopify');
              }}
            >
              <Icon name="plug" size="s" /> {connected ? 'Shopify settings' : 'Connect Shopify'}
            </button>
            <button
              className="pop-item"
              role="menuitem"
              onClick={() => {
                close();
                navigate('/sync-history');
              }}
            >
              <Icon name="clock" size="s" /> View sync history
            </button>
          </div>
        </div>

        <div style={{ position: 'relative' }}>
          <button
            className="icon-btn"
            onClick={() => setOpen(open === 'notifications' ? null : 'notifications')}
            aria-expanded={open === 'notifications'}
            aria-label="Notifications"
          >
            <Icon name="bell" />
          </button>

          <div className={`pop notif${open === 'notifications' ? ' on' : ''}`}>
            <div className="pop-hd" style={{ display: 'flex', alignItems: 'center' }}>
              <b style={{ fontSize: 13 }}>Notifications</b>
            </div>

            {status.loading ? (
              <div className="notif-empty">Checking…</div>
            ) : status.notices.length === 0 ? (
              // Only reached when every check actually passed, so it means
              // something now.
              <div className="notif-empty">You&rsquo;re all caught up.</div>
            ) : (
              status.notices.map((notice) => (
                <button
                  key={notice.key}
                  className="notif-item"
                  onClick={() => {
                    close();
                    navigate(notice.to);
                  }}
                >
                  <span className={`dot ${notice.tone}`} style={{ marginTop: 5 }} />
                  <span>
                    <b style={{ display: 'block', fontSize: 12.5 }}>{notice.title}</b>
                    <small style={{ color: 'var(--ink-45)', fontSize: 11.5 }}>
                      {notice.detail}
                    </small>
                  </span>
                </button>
              ))
            )}
          </div>
        </div>

        <div style={{ position: 'relative' }}>
          <button
            className="usermenu-btn"
            onClick={() => setOpen(open === 'user' ? null : 'user')}
            aria-expanded={open === 'user'}
            aria-haspopup="menu"
          >
            <span className="avatar">{user?.initials ?? '··'}</span>
            <span>{user?.full_name ?? ''}</span>
            <Icon name="down" size="s" style={{ opacity: 0.5 }} />
          </button>

          <div className={`pop${open === 'user' ? ' on' : ''}`} role="menu">
            <div className="pop-hd">
              <div style={{ fontWeight: 600, fontSize: 13 }}>{user?.full_name}</div>
              <div style={{ fontSize: 11.5, color: 'var(--ink-45)' }}>
                {user?.role} · {user?.email}
              </div>
            </div>
            <button
              className="pop-item"
              role="menuitem"
              onClick={() => {
                close();
                navigate('/settings/profile');
              }}
            >
              <Icon name="user" size="s" /> Profile
            </button>
            <button
              className="pop-item"
              role="menuitem"
              onClick={() => {
                close();
                navigate('/settings');
              }}
            >
              <Icon name="gear" size="s" /> Settings
            </button>
            <button
              className="pop-item"
              role="menuitem"
              onClick={() => {
                toggleTheme();
                toast(`Switched to ${theme === 'dark' ? 'light' : 'dark'} theme`, 'slate');
              }}
            >
              <Icon name={theme === 'dark' ? 'sun' : 'moon'} size="s" />
              <span>{theme === 'dark' ? 'Light' : 'Dark'} theme</span>
            </button>
            <div className="pop-sep" />
            <button className="pop-item" role="menuitem" onClick={() => void handleLogout()}>
              <Icon name="out" size="s" /> Log out
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

/**
 * The pill states what is true right now, in priority order: a store that is
 * not connected can't be "Synced", and a run in flight outranks the result of
 * the previous one.
 */
function pillState(status: ShopifyStatus): SyncState {
  if (status.loading) return 'checking';
  if (status.connection?.connected !== true) return 'idle';
  if (status.sync.state?.running) return 'busy';

  const result = status.sync.state?.run?.result;
  if (result === 'failed' || result === 'partial') return 'failed';
  if (result === 'success') return 'ok';
  // Connected, but nothing has ever run.
  return 'checking';
}
