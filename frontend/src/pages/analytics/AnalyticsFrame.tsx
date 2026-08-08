/**
 * The chrome every Analytics page shares.
 *
 * Title, the range control, and the two banners that can apply to any of them —
 * a failed load, and a rollup that has fallen behind the last sync. Extracted so
 * the five pages contain only what makes them different, and so a change to how
 * staleness is reported happens once.
 *
 * There is no Recompute here any more. A sync recomputes the figures before it
 * reports success, so staleness now means the automatic attempt failed — and the
 * next sync retries by itself. A button would ask the user to perform a repair
 * the system is already making.
 *
 * The empty state lives here too: with no imported sheet there is nothing for any
 * of the five to show, and each one inventing its own version of that screen
 * would be five chances to disagree about what to do next.
 */

import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { SyncStateNotice } from '../../components/SyncStateNotice';
import { RangePicker } from '../../components/charts/RangePicker';
import { Page } from '../../components/shell/Page';
import { PageHeader } from '../../components/shell/PageHeader';
import { ChartTooltipProvider } from '../../contexts/ChartTooltipContext';
import { freshness } from '../../lib/format';
import type { InsightsState } from './useInsights';

export function AnalyticsFrame({
  title,
  subtitle,
  state,
  children,
}: {
  title: string;
  /** Says what this page is for. Falls back to the freshness line. */
  subtitle: string;
  state: InsightsState;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  // `rebuild` is still on the state for whoever needs it; nothing on this
  // page calls it. A sync recomputes the figures before it reports success,
  // so there is no repair left for a user to make by hand.
  const { insights, error, range, setRange, reload } = state;

  if (insights && !insights.has_data) {
    return (
      <Page>
        <PageHeader title={title} subtitle={subtitle} />
        <div className="panel">
          <div className="empty">
            <div className="ei">
              <Icon name="chart" size="l" />
            </div>
            <h3>Nothing to analyse yet</h3>
            <p>
              Import an inventory sheet to begin. Once a store is connected, Shopify sales are
              matched onto your SKUs and every figure here follows from the two.
            </p>
            <div className="acts">
              <button className="btn pri" onClick={() => void navigate('/import')}>
                Import inventory
              </button>
              <button className="btn sec" onClick={() => void navigate('/shopify')}>
                Connect Shopify
              </button>
            </div>
          </div>
        </div>
      </Page>
    );
  }

  return (
    <ChartTooltipProvider>
      <Page>
        <PageHeader
          title={title}
          subtitle={
            insights?.last_computed_at
              ? `${subtitle} · computed ${freshness(new Date(insights.last_computed_at))}`
              : subtitle
          }
          actions={<RangePicker value={range} onChange={setRange} label={`${title} range`} />}
        />

        {error ? (
          <div style={{ marginBottom: 18 }}>
            <div className="inline-err">
              <Icon name="warn" />
              <div>
                <b>Couldn&rsquo;t load analytics.</b> {error}
              </div>
              <button className="btn sm" onClick={() => void reload()}>
                <Icon name="refresh" size="s" /> Retry
              </button>
            </div>
          </div>
        ) : null}

        <SyncStateNotice
          syncing={insights?.syncing}
          stale={insights?.stale}
          onRetryStarted={() => void reload()}
        />

        {children}
      </Page>
    </ChartTooltipProvider>
  );
}
