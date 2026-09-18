/**
 * The Shopify pair on the dashboard: the connection, and the state of the sync.
 *
 * Every figure here is live: the connection, the sync run and the sales totals
 * come from `/shopify/connection`, `/shopify/sync` and `/shopify/sales/summary`
 * through the shared status provider. Nothing is hardcoded, and no endpoint was
 * added — these are the same three the Shopify page already reads.
 *
 * **Two cards rather than one panel.** They answer two questions that fail
 * independently: a store can be connected and its last sync have failed, and a
 * sync can be running against a store whose token is about to be revoked. One
 * panel made the second question a footnote of the first.
 *
 * With no store connected there is one card again, not two empty ones: there is
 * no last sync and no order count to report, and showing those rows as "—"
 * would suggest a store that synced nothing.
 */

import { useNavigate } from 'react-router-dom';

import { useShopifyStatus } from '../hooks/useShopifyStatus';
import { freshness, n } from '../lib/format';
import { Icon } from './Icon';
import { Skeleton } from './Skeleton';
import { SyncResultBadge } from './StatusBadge';
import { storeGap } from './storeGap';
import { PanelHead } from './shell/PanelHead';

export function ShopifyWidget() {
  const navigate = useNavigate();
  const { connection, summary, sync, loading } = useShopifyStatus();

  if (loading) {
    return (
      <div className="dash-rail">
        {['Shopify Connection', 'Sync Status'].map((title) => (
          <div className="panel" key={title} aria-busy="true">
            <PanelHead title={title} icon="plug" />
            <div className="p-bd">
              <Skeleton height={12} width="60%" />
              <Skeleton height={18} width="80%" style={{ marginTop: 10 }} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  const store = connection?.connection ?? null;

  // `connected === false` is a real answer from the server; a failed read left
  // `connection` null, which is a different thing and must not be reported as
  // "no store connected".
  if (!connection || !connection.connected || !store) {
    return (
      <div className="dash-rail">
        <div className="panel">
          <PanelHead title="Shopify Connection" icon="plug" tone="amber" />
          <div className="empty sm">
            <div className="ei">
              <Icon name="plug" size="l" />
            </div>
            <h3>{connection ? 'No store connected' : 'Couldn’t check Shopify'}</h3>
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
      </div>
    );
  }

  const run = sync.state?.run ?? null;
  const running = sync.state?.running === true;
  const lastSyncedAt = summary?.last_synced_at ?? sync.state?.last_synced_at ?? null;

  // Mid-run the card shows the run's own counters, which climb as it works;
  // between runs it shows what is actually stored.
  const orders = running && run ? run.orders_synced : (summary?.orders ?? 0);
  const lineItems = running && run ? run.line_items_synced : (summary?.line_items ?? 0);

  /*
   * How far behind the live store we are — measured by every sync, stored on
   * the connection, and until now displayed nowhere. "Synced 33 minutes ago"
   * says when the sync ran, which is not the same as whether it got
   * everything.
   */
  const gap = storeGap(store.store_latest_order_at, lastSyncedAt, store.freshness_checked_at);

  return (
    <div className="dash-rail">
      <div className="panel">
        <PanelHead
          title="Shopify Connection"
          icon="plug"
          tone="moss"
          actions={
            <button className="btn sm" onClick={() => void navigate('/shopify')}>
              Manage Connection
            </button>
          }
        />
        <div className="p-bd">
          <div className="rail-val">{store.store_name ?? store.shop_domain}</div>
          <div className="rail-note">{store.shop_domain}</div>
          <span className="badge moss" style={{ marginTop: 10 }}>
            <span className="dot moss" />
            Connected
          </span>
        </div>
      </div>

      <div className="panel">
        <PanelHead
          title="Sync Status"
          icon="sync"
          tone={running ? 'slate' : run?.result === 'success' ? 'moss' : 'amber'}
          actions={
            running || run ? (
              <SyncResultBadge result={run?.result ?? null} running={running} />
            ) : null
          }
        />
        {/* Two labelled figures, each with the detail that qualifies it
            underneath — the label alone ("6,24,369") is a number without a
            question. The line-item counter belongs beside the order one in both
            states: mid-run they are the run's own climbing figures, and dropping
            one would make the card report half of what is happening. */}
        <div className="p-bd rail-stats four">
          <div className="rail-stat">
            <div className="stat-lbl">Last sync</div>
            <div className="rail-val sm">
              {running
                ? 'Syncing…'
                : lastSyncedAt
                  ? freshness(new Date(lastSyncedAt))
                  : 'Never'}
            </div>
            <div className="rail-note">
              {running
                ? 'In progress'
                : lastSyncedAt
                  ? new Date(lastSyncedAt).toLocaleString('en-IN')
                  : 'Run a sync to pull orders'}
            </div>
          </div>

          <div className="rail-stat">
            <div className="stat-lbl">Orders synced</div>
            <div className="rail-val sm num">{n(orders)}</div>
            <div className="rail-note">
              {n(lineItems)} line items · {n(summary?.skus_with_sales ?? 0)} SKUs with sales
            </div>
          </div>

          <div className="rail-stat">
            <div className="stat-lbl">Against Shopify</div>
            <div className="rail-val sm">
              <span
                className={`badge ${
                  gap.state === 'current' ? 'moss' : gap.state === 'behind' ? 'amber' : ''
                }`}
              >
                <span
                  className={`dot ${
                    gap.state === 'current' ? 'moss' : gap.state === 'behind' ? 'amber' : ''
                  }`}
                />
                {gap.state === 'current'
                  ? 'Up to date'
                  : gap.state === 'behind'
                    ? 'Behind'
                    : 'Unknown'}
              </span>
            </div>
            {/* The measurement's own age, because a gap read two days ago is
                not a statement about now. */}
            <div className="rail-note">
              {gap.checkedAt ? `${gap.label} · checked ${freshness(gap.checkedAt)}` : gap.label}
            </div>
          </div>

          <div className="rail-act">
            <button className="btn sm" onClick={() => void navigate('/sync-history')}>
              <Icon name="clock" size="s" /> View Sync History
            </button>
          </div>
          {/* No "Sync now" here. A sync runs after every successful import, so
              the routine path needs no button; the Shopify page keeps one for a
              refresh between imports and as the way back after a failure. */}
        </div>
      </div>
    </div>
  );
}
