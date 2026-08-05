/**
 * Settings → Shopify.
 *
 * The connection itself is made on the Shopify page, which stays the single
 * place a store is connected: this panel adjusts a store that already exists
 * and offers the two actions that need no credential typed in.
 *
 * The Admin API token is shown as a fixed mask, never a value. The API has
 * never returned it — it is encrypted at rest and not readable back — so there
 * is nothing here to reveal even if a field wanted to.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { ConnectionBadge } from '../../components/StatusBadge';
import { useShopifyStatus } from '../../hooks/useShopifyStatus';
import { useToast } from '../../hooks/useToast';
import { StockSyncApiError, api } from '../../lib/api';
import { freshness } from '../../lib/format';
import { LOOKBACK_DAYS } from '../../types/api';
import type { ConnectionState } from '../../types/api';

/** Not the token, and not a prefix of it — a fixed number of dots. */
const TOKEN_MASK = '•'.repeat(24);

export function ShopifySection() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { connection, loading, reload } = useShopifyStatus();

  const store = connection?.connection ?? null;
  const fromEnv = connection?.source === 'environment';

  const [days, setDays] = useState<number | null>(null);
  const [busy, setBusy] = useState<'save' | 'test' | 'disconnect' | null>(null);
  const [armed, setArmed] = useState(false);

  // Seeded from the server, and re-seeded whenever the server's value changes,
  // so a save elsewhere is not silently overwritten by a stale form.
  useEffect(() => {
    if (store) setDays(store.order_lookback_days);
  }, [store]);

  if (loading) {
    return (
      <div className="panel" aria-busy="true">
        <div className="p-hd">
          <h3>Shopify</h3>
        </div>
        <div className="p-bd">
          <Skeleton height={14} width="30%" />
          <Skeleton height={38} style={{ marginTop: 14 }} />
          <Skeleton height={38} style={{ marginTop: 14 }} />
        </div>
      </div>
    );
  }

  if (!connection || !connection.connected || !store) {
    return (
      <div className="panel">
        <div className="p-hd">
          <h3>Shopify</h3>
        </div>
        <div className="empty">
          <div className="ei">
            <Icon name="plug" size="l" />
          </div>
          <h3>No Shopify store connected</h3>
          <p>Connect a store to match its sales onto the SKUs in your sheet.</p>
          <div className="acts">
            <button className="btn pri" onClick={() => void navigate('/shopify')}>
              Connect Shopify
            </button>
          </div>
        </div>
      </div>
    );
  }

  const message = (caught: unknown, fallback: string) =>
    caught instanceof StockSyncApiError ? caught.message : fallback;

  async function save() {
    setBusy('save');
    try {
      await api.patch<ConnectionState>('/shopify/connection', { order_lookback_days: days });
      await reload();
      toast(`Sales are now matched over the last ${String(days)} days`, 'moss');
    } catch (caught) {
      toast(message(caught, "Couldn't save that setting."), 'rust', true);
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    setBusy('test');
    try {
      await api.post<ConnectionState>('/shopify/connection/verify');
      await reload();
      toast('Connection verified', 'moss');
    } catch (caught) {
      toast(message(caught, "Couldn't reach Shopify."), 'rust', true);
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy('disconnect');
    try {
      await api.delete('/shopify/connection');
      await reload();
      toast('Shopify store disconnected', 'slate');
    } catch (caught) {
      toast(message(caught, "Couldn't disconnect."), 'rust', true);
    } finally {
      setBusy(null);
      setArmed(false);
    }
  }

  const dirty = days !== null && days !== store.order_lookback_days;

  return (
    <div className="panel">
      <div className="p-hd">
        <h3>Shopify</h3>
        <div className="r">
          {fromEnv && <span className="badge">From .env</span>}
          <ConnectionBadge status={store.status} />
        </div>
      </div>

      <div className="p-bd">
        <div className="field">
          <label htmlFor="set-shop-url">Store URL</label>
          <input
            id="set-shop-url"
            className="inp"
            value={store.shop_domain}
            disabled
            readOnly
          />
          <div className="help">
            {store.store_name ? `${store.store_name} · ` : ''}
            Connecting a different store replaces this one, from the Shopify page.
          </div>
        </div>

        <div className="field">
          <label htmlFor="set-shop-token">Admin API access token</label>
          <input
            id="set-shop-token"
            className="inp"
            type="password"
            value={TOKEN_MASK}
            disabled
            readOnly
            aria-label="Admin API access token, hidden"
          />
          <div className="help">
            Stored encrypted and never shown again.{' '}
            {store.last_verified_at
              ? `Last checked ${freshness(new Date(store.last_verified_at))}.`
              : 'Not verified yet.'}
          </div>
        </div>

        <div className="field">
          <label htmlFor="set-lookback">Order lookback window</label>
          <select
            id="set-lookback"
            className="inp"
            value={days ?? store.order_lookback_days}
            onChange={(event) => setDays(Number(event.target.value))}
            disabled={fromEnv}
          >
            {LOOKBACK_DAYS.map((option) => (
              <option key={option} value={option}>
                Last {option} days
              </option>
            ))}
          </select>
          <div className="help">
            How far back orders are read. A longer window matches more sales and takes longer to
            sync.
          </div>
        </div>
      </div>

      <div className="p-ft">
        {!fromEnv && (
          <button
            className={`btn dgr${armed ? ' armed' : ''}`}
            onClick={() => void disconnect()}
            disabled={busy !== null}
          >
            {busy === 'disconnect'
              ? 'Disconnecting…'
              : armed
                ? 'Click again to confirm'
                : 'Disconnect store'}
          </button>
        )}
        <span className="spacer" />
        <button className="btn" onClick={() => void test()} disabled={busy !== null}>
          {busy === 'test' ? 'Checking…' : 'Test connection'}
        </button>
        <button
          className="btn cta"
          onClick={() => void save()}
          disabled={!dirty || busy !== null || fromEnv}
        >
          {busy === 'save' ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}
