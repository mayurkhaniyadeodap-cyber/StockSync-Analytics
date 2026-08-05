/**
 * The Shopify panel on the dashboard.
 *
 * Every figure here is live: the connection, the sync run and the sales totals
 * come from `/shopify/connection`, `/shopify/sync` and `/shopify/sales/summary`
 * through the shared status provider. Nothing is hardcoded, and no endpoint was
 * added — these are the same three the Shopify page already reads.
 *
 * The two states are deliberately different panels rather than one panel with
 * blanks: with no store connected there is no last sync and no order count, and
 * showing those rows as "—" would suggest a store that synced nothing.
 */

import { useNavigate } from 'react-router-dom';

import { useShopifyStatus } from '../hooks/useShopifyStatus';
import { freshness, n } from '../lib/format';
import { Icon } from './Icon';
import { Skeleton } from './Skeleton';

export function ShopifyWidget() {
  const navigate = useNavigate();
  const { connection, summary, sync, loading } = useShopifyStatus();

  if (loading) {
    return (
      <div className="panel" aria-busy="true">
        <div className="p-hd">
          <h3>Shopify</h3>
        </div>
        <div className="stat-row">
          {Array.from({ length: 4 }, (_, i) => (
            <div className="stat-cell" key={i}>
              <Skeleton height={10} width="55%" />
              <Skeleton height={18} width="70%" style={{ marginTop: 9 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const store = connection?.connection ?? null;

  // `connected === false` is a real answer from the server; a failed read left
  // `connection` null, which is a different thing and must not be reported as
  // "no store connected".
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
          <h3>{connection ? 'No Shopify store connected' : 'Couldn’t check Shopify'}</h3>
          <p>
            {connection
              ? 'Connect your store to match sales onto the SKUs in your sheet.'
              : 'The connection status could not be read. Your imported figures are unaffected.'}
          </p>
          <div className="acts">
            <button className="btn pri" onClick={() => void navigate('/shopify')}>
              <Icon name="plug" size="s" /> Connect Shopify
            </button>
          </div>
        </div>
      </div>
    );
  }

  const run = sync.state?.run ?? null;
  const running = sync.state?.running === true;
  const lastSyncedAt = summary?.last_synced_at ?? sync.state?.last_synced_at ?? null;

  // Mid-run the panel shows the run's own counters, which climb as it works;
  // between runs it shows what is actually stored.
  const orders = running && run ? run.orders_synced : (summary?.orders ?? 0);
  const lineItems = running && run ? run.line_items_synced : (summary?.line_items ?? 0);

  return (
    <div className="panel">
      <div className="p-hd">
        <h3>Shopify</h3>
        <div className="r">
          <span className="dot moss" />
          <span style={{ fontSize: 12.5, color: 'var(--ink-70)' }}>Connected</span>
        </div>
      </div>

      <div className="stat-row four">
        <div className="stat-cell">
          <div className="stat-lbl">Store</div>
          <div className="stat-val txt">{store.store_name ?? store.shop_domain}</div>
          <div className="stat-note">{store.shop_domain}</div>
        </div>

        <div className="stat-cell">
          <div className="stat-lbl">Last sync</div>
          <div className="stat-val txt">
            {running ? 'Syncing…' : lastSyncedAt ? freshness(new Date(lastSyncedAt)) : 'Never'}
          </div>
          <div className="stat-note">
            {running
              ? 'In progress'
              : lastSyncedAt
                ? new Date(lastSyncedAt).toLocaleString('en-IN')
                : 'Run a sync to pull orders'}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-lbl">Orders synced</div>
          <div className="stat-val num">{n(orders)}</div>
          <div className="stat-note">Rolling {store.order_lookback_days} days</div>
        </div>

        <div className="stat-cell">
          <div className="stat-lbl">Line items synced</div>
          <div className="stat-val num">{n(lineItems)}</div>
          <div className="stat-note">{n(summary?.skus_with_sales ?? 0)} SKUs with sales</div>
        </div>
      </div>

      <div className="p-ft">
        <span className="spacer" />
        <button className="btn sm" onClick={() => void navigate('/sync-history')}>
          <Icon name="clock" size="s" /> View sync history
        </button>
        {/* No "Sync now" here. A sync runs after every successful import, so
            the routine path needs no button; the Shopify page keeps one for a
            refresh between imports and as the way back after a failure. */}
      </div>
    </div>
  );
}
