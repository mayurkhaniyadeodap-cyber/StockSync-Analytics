/**
 * Analytics — the executive summary.
 *
 * Six figures, four findings, two small charts, and no tables. Everything here is
 * a pointer: if a card looks wrong, the page that explains it is one click away in
 * the sidebar. That constraint is the reason this page exists — the previous single
 * Analytics page held eight KPIs, five charts, seven tables and scrolled for
 * screens.
 *
 * The six cards are the dashboard's six deliberately. They are the figures every
 * other Analytics page is read against, so repeating them is context, not
 * duplication; the two derived averages live on the pages that use them.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { DonutChart } from '../../components/charts/DonutChart';
import { TrendScopeNote } from '../../components/TrendScopeNote';
import { LineChart } from '../../components/charts/LineChart';
import { CAT_COLORS } from '../../components/charts/palette';
import { n, pct } from '../../lib/format';
import type { QuickInsight } from '../../types/api';
import { AnalyticsFrame } from './AnalyticsFrame';
import { Card, Empty, InsightCard } from './parts';
import { labelsFor } from './status';
import { useInsights } from './useInsights';

export function OverviewPage() {
  const navigate = useNavigate();
  const state = useInsights();
  const { insights, loading } = state;
  const kpis = insights?.kpis;

  const cards = useMemo(() => {
    if (!kpis) return [];
    return [
      { label: 'Total SKUs', value: n(kpis.total_skus), note: 'in the imported sheet' },
      { label: 'Total Qty', value: n(kpis.total_qty), note: 'units on hand' },
      {
        label: 'Shopify Sales',
        value: n(kpis.shopify_sales),
        note: `units matched by SKU · ${String(insights?.days ?? 0)}d`,
      },
      {
        label: 'Shopify Sales %',
        value: pct(kpis.shopify_sales_pct),
        note: `of ${n(kpis.shopify_sales_all)} units sold`,
      },
      { label: 'Total Orders', value: n(kpis.total_orders), note: 'from the sheet' },
      {
        label: 'Total Complaints',
        value: n(kpis.total_complaints),
        note: 'across all categories',
        tone: kpis.total_complaints > 0 ? ('warn' as const) : undefined,
      },
    ];
  }, [insights, kpis]);

  /**
   * The four findings this page promises, assembled from the payload rather than
   * fetched separately.
   *
   * Two come from the generated cards; two are the extremes the sales and
   * complaint sections already computed. Building them here keeps one definition
   * of "best selling" on the server and lets this page choose which four of the
   * six findings belong on a summary.
   */
  const findings = useMemo((): QuickInsight[] => {
    if (!insights) return [];
    const found: QuickInsight[] = [];
    const { sales, complaints, inventory, quick } = insights;

    if (sales.highest) {
      found.push({
        key: 'bestselling',
        icon: 'check',
        title: 'Best selling SKU',
        sku: sales.highest.sku,
        value: `${n(sales.highest.shopify_sales)} units`,
        note: `${pct(sales.highest.shopify_sales_pct)} of everything the store sold`,
      });
    }
    if (complaints.most_complained) {
      const worst = complaints.most_complained;
      found.push({
        key: 'complaints',
        icon: 'warn',
        title: 'Highest complaint SKU',
        sku: worst.sku,
        value: `${n(worst.total_complaints)} complaints`,
        note:
          worst.total_orders > 0
            ? `against ${n(worst.total_qty)} units of stock`
            : 'no orders recorded in the sheet',
      });
    }
    // "Low stock alert" is the restock finding: selling more than it holds.
    const restock = quick.find((card) => card.key === 'restock');
    if (restock) found.push({ ...restock, title: 'Low stock alert' });

    if (inventory.zero_sales.length > 0) {
      const worst = inventory.zero_sales[0];
      found.push({
        key: 'nosales',
        icon: 'x',
        title: 'Zero sales SKU',
        sku: worst?.sku ?? null,
        value: `${n(inventory.zero_sales_total)} SKUs`,
        note: `holding stock but unsold — ${worst?.sku ?? ''} has the most at ${n(
          worst?.total_qty ?? 0,
        )}`,
      });
    }
    return found;
  }, [insights]);

  const complaintMix = insights?.complaints.categories.filter((c) => c.count > 0) ?? [];

  return (
    <AnalyticsFrame title="Analytics" subtitle="Executive summary" state={state}>
      <div className="cardgrid">
        {loading
          ? Array.from({ length: 6 }, (_, i) => (
              <div className="kpi static" key={i} aria-busy="true">
                <Skeleton height={11} width="60%" />
                <Skeleton height={20} width="80%" style={{ marginTop: 10 }} />
                <Skeleton height={9} width="50%" style={{ marginTop: 8 }} />
              </div>
            ))
          : cards.map((card) => <Card key={card.label} {...card} />)}
      </div>

      <div className="panel">
        <div className="p-hd">
          <h3>Quick insights</h3>
          <span className="hint">Generated from this window&rsquo;s figures</span>
        </div>
        {loading ? (
          <div className="p-bd" aria-busy="true">
            <Skeleton height={70} />
          </div>
        ) : findings.length === 0 ? (
          <Empty note="No findings yet — import a sheet and sync a store." />
        ) : (
          <div className="p-bd">
            <div className="cardgrid four" style={{ marginBottom: 0 }}>
              {findings.map((finding) => (
                <InsightCard key={finding.key} insight={finding} />
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="p-hd">
            <h3>Sales trend</h3>
            <button className="btn sm" onClick={() => void navigate('/analytics/sales')}>
              Sales analytics <Icon name="right" size="s" />
            </button>
          </div>
          {insights === null ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={150} />
            </div>
          ) : insights.trend.points.every((point) => point.units === 0) ? (
            <Empty note="No sales landed in this window, so there is no trend to plot yet." />
          ) : (
            <>
              <div className="chart-wrap">
                <LineChart
                  caption={`Units sold per day over the last ${String(state.range)} days`}
                  labels={labelsFor(insights.trend)}
                  series={[
                    {
                      name: 'Units sold',
                      color: 'var(--slate)',
                      values: insights.trend.points.map((p) => p.units),
                      fill: true,
                    },
                  ]}
                />
              </div>
              {/* A sibling of the chart, not a child: .chart-wrap carries its
                  own padding and is the positioning context for tooltips. */}
              <TrendScopeNote />
            </>
          )}
        </div>

        <div className="panel">
          <div className="p-hd">
            <h3>Complaint mix</h3>
            <button className="btn sm" onClick={() => void navigate('/analytics/complaints')}>
              Complaint analytics <Icon name="right" size="s" />
            </button>
          </div>
          {insights === null ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={150} />
            </div>
          ) : complaintMix.length === 0 ? (
            <Empty note="No complaints are recorded in the imported sheet." />
          ) : (
            <>
              <div className="chart-wrap">
                <DonutChart
                  caption="Complaints by category"
                  centerValue={n(insights.complaints.total_complaints)}
                  centerLabel="complaints"
                  slices={complaintMix.map((category, index) => ({
                    label: category.label,
                    value: category.count,
                    color: CAT_COLORS[index % CAT_COLORS.length] as string,
                  }))}
                />
              </div>
              {/* The sheet carries complaint totals per SKU with no dates, so a
                  complaint *trend* cannot be drawn from it. Saying so is better
                  than an empty axis or a fabricated line. */}
              <div className="p-ft">
                <span className="hint">
                  The sheet records complaint totals per SKU without dates, so complaints are
                  shown as a mix rather than over time.
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </AnalyticsFrame>
  );
}
