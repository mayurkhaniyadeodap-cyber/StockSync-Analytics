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
import { Logo } from '../Logo';

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

type OpenPopover = 'sync' | 'user' | null;

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

      {/*
        The brand, in the corner it belongs in.

        It moved here from the rail rather than being added alongside it: the
        header spans the full width *above* the rail, so a lockup in both put
        two identical ones thirty pixels apart. Here it also survives the rail
        collapsing to 72px and the drawer closing on a phone, which the rail's
        copy did not.
      */}
      <button
        className="hdr-brand"
        onClick={() => navigate('/dashboard')}
        aria-label="StockSync Analytics — go to dashboard"
      >
        <Logo size={32} wordmark />
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
            {/* Two lines: the state, and when it was last true. "Synced" alone
                is only half an answer — synced *when* is the part that decides
                whether to trust the figures below. */}
            <span className="sl">
              <b>{sync.label}</b>
              {lastSyncedAt ? <small>{freshness(new Date(lastSyncedAt))}</small> : null}
            </span>
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
