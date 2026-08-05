import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../components/Icon';
import { Skeleton } from '../components/Skeleton';
import { RetrySyncButton } from '../components/RetrySyncButton';
import { ConnectionBadge, SyncResultBadge } from '../components/StatusBadge';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import type { UseSync } from '../hooks/useSync';
import { useShopifyStatus } from '../hooks/useShopifyStatus';
import { useToast } from '../hooks/useToast';
import { StockSyncApiError, api } from '../lib/api';
import type {
  SalesSummary,
  ConnectionState,
  ShopProfile,
  SyncHistoryPage,
  TestConnectionResult,
} from '../types/api';

/**
 * Design doc §9.
 *
 * M2 covers the credential only — store URL, Admin API token, Test Connection,
 * Save and Disconnect (M2), then Sync now, staged progress (§9.3), recent
 * syncs and the inventory comparison (M3).
 */
export function ShopifyPage() {
  const [state, setState] = useState<ConnectionState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { reload: reloadStatus } = useShopifyStatus();

  const load = useCallback(async () => {
    try {
      setState(await api.get<ConnectionState>('/shopify/connection'));
      setLoadError(null);
    } catch (error) {
      setLoadError(
        error instanceof StockSyncApiError ? error.message : 'Could not load the connection.',
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loadError) {
    return (
      <Page>
        <PageHeader
          title="Shopify connection"
          subtitle="Connect a store to match sales by SKU"
        />
        <div className="panel">
          <div className="p-bd">
            <div className="inline-err">
              <Icon name="warn" />
              <div>{loadError}</div>
              <button className="btn sm" onClick={() => void load()}>
                Retry
              </button>
            </div>
          </div>
        </div>
      </Page>
    );
  }

  if (state === null) return <ConnectionSkeleton />;

  return (
    <ShopifyBody
      state={state}
      onChanged={() => {
        void load();
        // The header and the dashboard read the shared status, and the provider
        // is mounted once at the shell — without this, connecting a store here
        // and navigating to the dashboard would still show "not connected".
        void reloadStatus();
      }}
    />
  );
}

/**
 * Everything below the loading guard.
 *
 * Split out so its hooks run unconditionally: the parent returns early while
 * the connection loads, and a hook after an early return is a rules-of-hooks
 * violation waiting to bite the next person who adds state up there.
 */
function ShopifyBody({ state, onChanged }: { state: ConnectionState; onChanged: () => void }) {
  const { toast } = useToast();
  const connection = state.connection;
  const live = state.connected;
  // Configured in .env rather than stored here: there is no row, so Disconnect
  // has nothing to act on and offering it would be a button that only errors.
  const fromEnv = state.source === 'environment';
  // The shell's sync rather than a second poller of the same endpoint, so a
  // sync started here is the one the header pill and the dashboard show.
  const { sync } = useShopifyStatus();

  return (
    <Page>
      <PageHeader
        title="Shopify connection"
        subtitle={
          live && connection ? (
            <>
              <span className="dot moss" /> Connected
            </>
          ) : (
            'Connect a store to match sales by SKU'
          )
        }
        actions={
          live && !fromEnv ? (
            <DisconnectButton
              onDone={() => {
                toast('Shopify store disconnected', 'slate');
                onChanged();
              }}
            />
          ) : undefined
        }
      />

      {live && connection && (
        <>
          <StoreCard
            connection={connection}
            fromEnv={fromEnv}
            onChanged={onChanged}
            sync={sync}
          />

          {sync.error && (
            <div className="panel">
              <div className="p-bd">
                <div className="inline-err">
                  <Icon name="warn" />
                  <div>{sync.error}</div>
                </div>
              </div>
            </div>
          )}

          <RecentSyncs refreshKey={sync.completedAt} />
        </>
      )}

      {/* Only when nothing is connected. The target shows the connected page,
          which has no form on it — but without this there would be no way to
          connect a store at all, so it stays on the empty state. */}
      {!live && (
        <ConnectForm
          previous={connection}
          showEmpty
          onConnected={(profile) => {
            toast(`Connected to ${profile.store_name ?? profile.shop_domain}`, 'moss');
            onChanged();
          }}
        />
      )}
    </Page>
  );
}

function ConnectionSkeleton() {
  return (
    <Page>
      <PageHeader title="Shopify connection" subtitle="Connect a store to match sales by SKU" />
      <div className="panel" aria-busy="true">
        <div className="p-bd">
          <Skeleton height={14} width="30%" />
          <Skeleton height={38} style={{ marginTop: 14 }} />
          <Skeleton height={38} style={{ marginTop: 14 }} />
        </div>
      </div>
    </Page>
  );
}

function DisconnectButton({ onDone }: { onDone: () => void }) {
  const { toast } = useToast();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  // Two-step rather than a dialog: .btn.dgr.armed is the pattern the design
  // system already carries for a destructive action.
  async function disconnect() {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    try {
      await api.delete('/shopify/connection');
      onDone();
    } catch (error) {
      toast(
        error instanceof StockSyncApiError ? error.message : 'Could not disconnect.',
        'rust',
        true,
      );
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }

  return (
    <button
      className={`btn dgr${armed ? ' armed' : ''}`}
      onClick={() => void disconnect()}
      disabled={busy}
    >
      {armed ? 'Confirm disconnect' : 'Disconnect'}
    </button>
  );
}

/**
 * The store card — one bordered panel, four columns, a divider between each.
 *
 * The connection supplies the name and the domain; `/shopify/sales/summary` the
 * one field left on it that matters here, when the store was last pulled.
 *
 * It used to also read `/shopify/freshness` and report how far behind the live
 * store the synced orders were, in hours. That question answers itself now:
 * a sync follows every import, so the figures beside the SKUs just imported are
 * current by construction, and an hours-behind number was Shopify bookkeeping
 * on a page about whether the connection works.
 */

/** When the store was last pulled successfully, or that it never has been. */
function lastSyncedLabel(at: string | null | undefined): string {
  if (!at) return 'Never';
  return new Date(at).toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * What the sync is doing right now, in the words a reader needs.
 *
 * Deliberately not a progress bar, a page count or an order tally: this page
 * answers "is my store connected, and are its sales current". The figures those
 * numbers feed live on the Analytics pages, where they mean something.
 */
function syncStatus(sync: UseSync): { label: string; tone: string; failed: boolean } {
  if (sync.state?.running) return { label: 'Running', tone: 'amber', failed: false };
  const result = sync.state?.run?.result;
  if (result === 'failed') return { label: 'Failed', tone: 'rust', failed: true };
  // Partial is the shape a recompute failure takes: the orders arrived and the
  // figures did not, which is a retry away and not a re-import.
  if (result === 'partial') {
    return { label: 'Partial — analytics update failed', tone: 'amber', failed: true };
  }
  if (result === 'success') return { label: 'Success', tone: 'moss', failed: false };
  return { label: 'Not run yet', tone: 'slate', failed: false };
}

function StoreCard({
  connection,
  fromEnv,
  onChanged,
  sync,
}: {
  connection: NonNullable<ConnectionState['connection']>;
  fromEnv: boolean;
  onChanged: () => void;
  sync: UseSync;
}) {
  const { toast } = useToast();
  const [verifying, setVerifying] = useState(false);
  const [summary, setSummary] = useState<SalesSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Read for one field: when the store was last pulled successfully. The
    // order and line-item counts it also carries are Shopify's own bookkeeping
    // and belong on neither this page nor any other.
    api
      .get<SalesSummary>('/shopify/sales/summary')
      .then((next) => {
        if (!cancelled) setSummary(next);
      })
      .catch(() => {
        if (!cancelled) setSummary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [connection.shop_domain, sync.completedAt]);

  async function verify() {
    setVerifying(true);
    try {
      await api.post('/shopify/connection/verify');
      toast('Connection verified', 'moss');
    } catch (error) {
      toast(
        error instanceof StockSyncApiError ? error.message : 'Could not verify the connection.',
        'rust',
        true,
      );
    } finally {
      setVerifying(false);
      onChanged();
    }
  }

  const status = syncStatus(sync);

  return (
    <div className="panel">
      <div className="p-hd">
        <h3>Store</h3>
        <div className="r">
          {fromEnv && <span className="badge">From .env</span>}
          <ConnectionBadge status={connection.status} />
          <button className="btn sm" onClick={() => void verify()} disabled={verifying}>
            {verifying ? 'Checking…' : 'Test connection'}
          </button>
        </div>
      </div>

      {/* Four cells, and each answers a question about the connection itself.
          Orders pulled, the rolling window, token scopes and the freshness
          comparison were removed: they described how Shopify was being talked
          to, not whether the SKUs on the Analytics pages have sales behind
          them. */}
      <div className="stat-row">
        <div className="stat-cell">
          <div className="stat-lbl">Store name</div>
          <div className="stat-val txt">{connection.store_name ?? 'Not reported'}</div>
          <div className="stat-note">
            {connection.store_name ? 'From Shopify' : 'Run Test connection to read it'}
          </div>
        </div>

        <div className="stat-cell">
          <div className="stat-lbl">Store URL</div>
          <div className="stat-val txt">{connection.shop_domain}</div>
          <div className="stat-note">Sales are matched to your SKUs from this store</div>
        </div>

        <div className="stat-cell">
          <div className="stat-lbl">Last successful sync</div>
          <div className="stat-val txt">{lastSyncedLabel(summary?.last_synced_at)}</div>
          <div className="stat-note">A sync runs after every successful import</div>
        </div>

        <div className="stat-cell">
          <div className="stat-lbl">Sync status</div>
          <div className="stat-val txt">
            <span className={`dot ${status.tone}`} /> {status.label}
          </div>
          <div className="stat-note">
            {/* The one place a failure is visible outside the import screen the
                user has probably left. The retry repeats only what failed and
                never asks for the sheet again. */}
            {status.failed ? (
              <RetrySyncButton
                onStarted={() => sync.refresh()}
                running={Boolean(sync.state?.running)}
              />
            ) : sync.state?.running ? (
              'Pulling orders from Shopify'
            ) : (
              'Updates automatically after an import'
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ConnectForm({
  previous,
  showEmpty,
  onConnected,
}: {
  previous: ConnectionState['connection'];
  /** False when a store is already live — the §9.4 empty state would contradict it. */
  showEmpty: boolean;
  onConnected: (profile: ShopProfile) => void;
}) {
  const [shopUrl, setShopUrl] = useState(previous?.shop_domain ?? '');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState<'test' | 'save' | null>(null);
  const [banner, setBanner] = useState<{ message: string; next: string } | null>(null);
  const [profile, setProfile] = useState<ShopProfile | null>(null);

  const ready = shopUrl.trim().length > 0 && token.trim().length > 0;

  function fail(error: unknown) {
    setProfile(null);
    setBanner(
      error instanceof StockSyncApiError
        ? { message: error.message, next: error.next }
        : {
            message: 'Connection failed — check the store URL and token permissions.',
            next: 'Confirm the token is correct, then try again.',
          },
    );
  }

  async function test() {
    setBusy('test');
    setBanner(null);
    try {
      const result = await api.post<TestConnectionResult>('/shopify/connection/test', {
        shop_url: shopUrl.trim(),
        access_token: token.trim(),
      });
      setProfile(result.profile);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy('save');
    setBanner(null);
    try {
      const result = await api.post<ConnectionState>('/shopify/connection', {
        shop_url: shopUrl.trim(),
        access_token: token.trim(),
      });
      if (result.connection) {
        onConnected({
          shop_domain: result.connection.shop_domain,
          store_name: result.connection.store_name,
          plan_name: result.connection.plan_name,
          currency: result.connection.currency,
          scopes: result.connection.token_scopes?.split(',') ?? [],
        });
      }
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {showEmpty && !previous && (
        <div className="panel">
          <div className="empty">
            <div className="ei">
              <Icon name="plug" size="l" />
            </div>
            <h3>No store connected</h3>
            <p>
              Connect your Shopify store to start matching sales against your inventory sheet.
            </p>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="p-hd">
          <h3>Connect a store</h3>
          <span className="hint">The token is stored encrypted and never shown again</span>
        </div>
        <div className="p-bd">
          {banner && (
            <div className="banner err" role="alert">
              <Icon name="warn" size="s" style={{ marginTop: 2 }} />
              <span>
                <b>{banner.message}</b> {banner.next}
              </span>
            </div>
          )}

          <div className="field">
            <label htmlFor="shop-url">
              Store URL <span className="req">*</span>
            </label>
            <input
              id="shop-url"
              className="inp"
              value={shopUrl}
              onChange={(e) => {
                setShopUrl(e.target.value);
                setProfile(null);
              }}
              placeholder="mystore.myshopify.com"
              autoComplete="off"
              disabled={busy !== null}
            />
            <div className="help">The myshopify.com address, not a custom domain.</div>
          </div>

          <div className="field">
            <label htmlFor="shop-token">
              Admin API access token <span className="req">*</span>
            </label>
            <input
              id="shop-token"
              className="inp"
              type="password"
              value={token}
              onChange={(e) => {
                setToken(e.target.value);
                setProfile(null);
              }}
              placeholder="shpat_…"
              autoComplete="off"
              disabled={busy !== null}
            />
            <div className="help">Needs read_orders.</div>
          </div>

          {profile && (
            <div className="filechip" style={{ marginTop: 4 }}>
              <span className="fi">
                <Icon name="check" size="s" />
              </span>
              <div>
                <b>{profile.store_name ?? profile.shop_domain}</b>
                <div className="help" style={{ marginTop: 2 }}>
                  {[profile.shop_domain, profile.plan_name, profile.currency]
                    .filter(Boolean)
                    .join(' · ')}
                  {profile.scopes.length ? ` · ${profile.scopes.join(', ')}` : ''}
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="p-ft">
          <span className="hint">
            {profile ? 'Connection verified. Save it to finish.' : 'Test it before saving.'}
          </span>
          <span className="spacer" />
          <button
            className="btn"
            onClick={() => void test()}
            disabled={!ready || busy !== null}
          >
            {busy === 'test' ? 'Testing…' : 'Test connection'}
          </button>
          <button
            className="btn cta"
            onClick={() => void save()}
            disabled={!ready || busy !== null}
          >
            {busy === 'save' ? 'Saving…' : 'Save connection'}
          </button>
        </div>
      </div>
    </>
  );
}

/** Design doc §9.1: the four most recent syncs, with a link to the full log. */
/** Design doc §15: status is a 3px left-edge bar, not a full-row tint. */
const ROW_TONES: Record<string, string> = {
  success: 's-ok',
  partial: 's-warn',
  failed: 's-bad',
};

function RecentSyncs({ refreshKey }: { refreshKey: number }) {
  const navigate = useNavigate();
  const [page, setPage] = useState<SyncHistoryPage | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<SyncHistoryPage>('/shopify/syncs?limit=4')
      .then((next) => {
        if (!cancelled) setPage(next);
      })
      .catch(() => {
        if (!cancelled) setPage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  return (
    <div className="panel">
      <div className="p-hd">
        <h3>Recent syncs</h3>
        <div className="r">
          <button className="btn sm" onClick={() => navigate('/sync-history')}>
            Full history
          </button>
        </div>
      </div>
      {page === null ? (
        // A row-shaped skeleton rather than one bar: the table is what is
        // arriving, so the placeholder should be that shape (design doc §18).
        <div className="p-bd" aria-busy="true">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
          ))}
        </div>
      ) : page.items.length === 0 ? (
        <div className="empty">
          <div className="ei">
            <Icon name="sync" size="l" />
          </div>
          <h3>No syncs yet</h3>
          <p>Import a sheet and Shopify sales will be pulled in automatically.</p>
        </div>
      ) : (
        <div className="tbl-scroll">
          <table className="tbl">
            {/* When and how it went. The order count and the duration were
                removed: they are Shopify's bookkeeping, and this page is about
                whether the sales behind your SKUs are current. */}
            <thead>
              <tr>
                <th>Date</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((run) => (
                <tr key={run.id} className={ROW_TONES[run.result ?? ''] ?? ''}>
                  <td data-l="Date">
                    {new Date(run.started_at).toLocaleString('en-IN', {
                      day: '2-digit',
                      month: 'short',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td data-l="Result">
                    <SyncResultBadge result={run.result} running={run.is_running} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
