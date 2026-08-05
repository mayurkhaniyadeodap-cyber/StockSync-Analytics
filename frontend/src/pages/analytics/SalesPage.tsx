/**
 * Sales Analytics — everything Shopify contributes.
 *
 * Shopify is a sales source and nothing else: units sold, joined to a sheet SKU on
 * the normalised code. There is no product, variant or vendor here to describe,
 * which is why every figure on the page is a count of units or a share of them.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { TrendScopeNote } from '../../components/TrendScopeNote';
import { BarChart } from '../../components/charts/BarChart';
import { DonutChart } from '../../components/charts/DonutChart';
import { LineChart } from '../../components/charts/LineChart';
import { CAT_COLORS } from '../../components/charts/palette';
import { n, pct } from '../../lib/format';
import { AnalyticsFrame } from './AnalyticsFrame';
import { Card, Empty } from './parts';
import { labelsFor } from './status';
import { useInsights } from './useInsights';

export function SalesPage() {
  const navigate = useNavigate();
  const state = useInsights();
  const { insights, loading, range } = state;

  const cards = useMemo(() => {
    if (!insights) return [];
    const { kpis, sales } = insights;
    return [
      {
        label: 'Shopify Sales',
        value: n(sales.shopify_sales),
        note: `units matched by SKU · ${String(insights.days)}d`,
      },
      {
        label: 'Shopify Sales %',
        value: pct(sales.shopify_sales_pct),
        note: `of ${n(kpis.shopify_sales_all)} units sold`,
      },
      {
        label: 'Avg Sales per SKU',
        value: n(Math.round(kpis.avg_sales_per_sku * 10) / 10),
        note: `${n(kpis.shopify_sales)} units over ${n(kpis.total_skus)} SKUs`,
      },
      {
        label: 'Highest Selling SKU',
        value: sales.highest?.sku ?? '—',
        note: sales.highest ? `${n(sales.highest.shopify_sales)} units` : 'nothing sold yet',
      },
    ];
  }, [insights]);

  const sales = insights?.sales;
  const noSales = sales !== undefined && sales.shopify_sales === 0;

  return (
    <AnalyticsFrame
      title="Sales Analytics"
      subtitle="Shopify units, matched by SKU"
      state={state}
    >
      <div className="cardgrid four">
        {loading
          ? Array.from({ length: 4 }, (_, i) => (
              <div className="kpi static" key={i} aria-busy="true">
                <Skeleton height={11} width="60%" />
                <Skeleton height={20} width="80%" style={{ marginTop: 10 }} />
                <Skeleton height={9} width="50%" style={{ marginTop: 8 }} />
              </div>
            ))
          : cards.map((card) => (
              // The card holding a SKU code needs the mono treatment the figures
              // do not; `mono` is passed rather than inferred from the value.
              <Card key={card.label} {...card} mono={card.label === 'Highest Selling SKU'} />
            ))}
      </div>

      {/* Stated on the page whose subject is sales, and on every report that
          carries them. Shopify reports a refund against the order, and line
          items are stored without a refunded quantity, so there is nothing to
          subtract per SKU — the figure is knowably a little high rather than
          exact, and saying so beats a number that looks precise. */}
      <div className="notice" style={{ marginBottom: 18 }}>
        <Icon name="warn" size="s" style={{ marginTop: 2 }} />
        <span>
          Partially refunded orders are counted in full; fully refunded, voided and cancelled
          orders are excluded. Shopify does not report a refunded quantity per line item, so the
          refunded part of a partial refund cannot be deducted.
        </span>
      </div>

      <div className="panel">
        <div className="p-hd">
          <h3>Sales trend</h3>
          <span className="hint">Units per day, against the preceding {range} days</span>
        </div>
        {insights === null ? (
          <div className="p-bd" aria-busy="true">
            <Skeleton height={190} />
          </div>
        ) : insights.trend.points.every((point) => point.units === 0) ? (
          <Empty note="No sales landed in this window, so there is no trend to plot yet." />
        ) : (
          <>
            <div className="chart-wrap">
              <LineChart
                caption={`Units sold per day over the last ${String(range)} days`}
                labels={labelsFor(insights.trend)}
                series={[
                  {
                    name: 'Units sold',
                    color: 'var(--slate)',
                    values: insights.trend.points.map((p) => p.units),
                    fill: true,
                  },
                  {
                    name: 'Prior period',
                    color: 'var(--moss)',
                    values: insights.trend.previous.map((p) => p.units),
                    dashed: true,
                  },
                ]}
              />
            </div>
            <div className="legend">
              <span>
                <i style={{ background: 'var(--slate)' }} />
                Units sold
              </span>
              <span>
                <i style={{ background: 'var(--moss)' }} />
                Prior period
              </span>
            </div>
            <TrendScopeNote />
          </>
        )}
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="p-hd">
            <h3>Sales distribution</h3>
            <span className="hint">Share of matched units</span>
          </div>
          {sales === undefined ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={180} />
            </div>
          ) : sales.distribution.length === 0 ? (
            <Empty note="Nothing has sold, so there is no distribution to show." />
          ) : (
            <>
              <div className="chart-wrap">
                <DonutChart
                  caption="How sales are spread across SKUs"
                  centerValue={n(sales.shopify_sales)}
                  centerLabel="units"
                  slices={sales.distribution.map((slice, index) => ({
                    label: slice.label,
                    value: slice.count,
                    color: CAT_COLORS[index % CAT_COLORS.length] as string,
                  }))}
                />
              </div>
              <div className="legend">
                {sales.distribution.slice(0, 6).map((slice, index) => (
                  <span key={slice.field_name}>
                    <i
                      style={{ background: CAT_COLORS[index % CAT_COLORS.length] as string }}
                    />
                    {slice.label} · {pct(slice.share_pct)}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="panel">
          <div className="p-hd">
            <h3>Top selling SKUs</h3>
            <span className="hint">By units</span>
          </div>
          {sales === undefined ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={180} />
            </div>
          ) : sales.top.length === 0 ? (
            <Empty note="No SKU in the sheet has sold in this window." />
          ) : (
            <div className="chart-wrap">
              <BarChart
                caption={`Units sold per SKU, top ${String(sales.top.length)}`}
                color="var(--slate)"
                rows={sales.top.map((row) => ({
                  label: row.sku,
                  value: row.shopify_sales,
                  note: pct(row.shopify_sales_pct),
                }))}
              />
            </div>
          )}
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="p-hd">
            <h3>Lowest selling SKUs</h3>
            <span className="hint">Slowest movers first</span>
          </div>
          {insights === null ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={180} />
            </div>
          ) : noSales ? (
            <Empty note="Nothing has sold, so there is nothing to rank." />
          ) : (
            <div className="chart-wrap">
              <BarChart
                caption="The slowest-moving SKUs by units sold"
                color="var(--clay)"
                rows={insights.rankings.lowest_selling.map((row) => ({
                  label: row.sku,
                  value: row.shopify_sales,
                  note: `${n(row.total_qty)} held`,
                }))}
              />
            </div>
          )}
        </div>

        <div className="panel">
          <div className="p-hd">
            <h3>Every SKU</h3>
            <span className="hint">Sales, complaints and every category, in one table</span>
          </div>
          {/* The per-SKU figures live in one consolidated table now, so this page
            keeps the shapes and sends you there for the rows. */}
          <div className="p-bd" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="hint">Every SKU figure, in one table.</span>
            <span className="spacer" />
            <button className="btn sm" onClick={() => void navigate('/analytics/performance')}>
              Open SKU performance <Icon name="right" size="s" />
            </button>
          </div>
        </div>
      </div>
    </AnalyticsFrame>
  );
}
