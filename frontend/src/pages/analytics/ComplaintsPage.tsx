/**
 * Complaint Analytics — the ten categories the sheet carries.
 *
 * Complaints come entirely from the imported sheet: ten integer columns per SKU.
 * **Whether they carry dates depends on the file.** A complaint export has one
 * row per complaint with the day on it, so those figures answer the selected
 * range; an aggregated sheet has no date column, so its totals stand in every
 * range and `ComplaintScopeNote` says so. Every view here is a different cut of
 * the same numbers: by category and by SKU.
 *
 * The counts carry their share or rate beside them rather than in a tooltip
 * only: 412 complaints means little until you know it is 38% of them.
 */

import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { ComplaintScopeNote } from '../../components/ComplaintScopeNote';
import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { BarChart } from '../../components/charts/BarChart';
import { DonutChart } from '../../components/charts/DonutChart';
import { CAT_COLORS } from '../../components/charts/palette';
import { n, pct } from '../../lib/format';
import { AnalyticsFrame } from './AnalyticsFrame';
import { Card, Empty } from './parts';
import { useInsights } from './useInsights';

export function ComplaintsPage() {
  const navigate = useNavigate();
  const state = useInsights();
  const { insights, loading } = state;

  const cards = useMemo(() => {
    if (!insights) return [];
    const { complaints, kpis } = insights;
    return [
      {
        label: 'Total Complaints',
        value: n(complaints.total_complaints),
        note: 'across all ten categories',
        tone: complaints.total_complaints > 0 ? ('warn' as const) : undefined,
      },
      {
        label: 'Most Complained SKU',
        value: complaints.most_complained?.sku ?? '—',
        note: complaints.most_complained
          ? `${n(complaints.most_complained.total_complaints)} complaints`
          : 'none logged',
        mono: true,
      },
      {
        label: 'Affected SKUs',
        value: n(complaints.skus_with_complaints),
        note: `of ${n(kpis.total_skus)} carried`,
      },
    ];
  }, [insights]);

  const complaints = insights?.complaints;
  const withCounts = complaints?.categories.filter((category) => category.count > 0) ?? [];

  return (
    <AnalyticsFrame
      title="Complaint Analytics"
      subtitle="From the imported sheet's own columns"
      state={state}
    >
      <ComplaintScopeNote scope={insights?.complaint_scope} />

      <div className="cardgrid four">
        {loading
          ? Array.from({ length: 4 }, (_, i) => (
              <div className="kpi static" key={i} aria-busy="true">
                <Skeleton height={11} width="60%" />
                <Skeleton height={20} width="80%" style={{ marginTop: 10 }} />
                <Skeleton height={9} width="50%" style={{ marginTop: 8 }} />
              </div>
            ))
          : cards.map((card) => <Card key={card.label} {...card} />)}
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="p-hd">
            <h3>Complaint distribution</h3>
            <span className="hint">Share by category</span>
          </div>
          {complaints === undefined ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={180} />
            </div>
          ) : withCounts.length === 0 ? (
            <Empty note="No complaints are recorded in the imported sheet." />
          ) : (
            <>
              <div className="chart-wrap">
                <DonutChart
                  caption="How complaints are spread across categories"
                  centerValue={n(complaints.total_complaints)}
                  centerLabel="complaints"
                  slices={withCounts.map((category, index) => ({
                    label: category.label,
                    value: category.count,
                    color: CAT_COLORS[index % CAT_COLORS.length] as string,
                  }))}
                />
              </div>
              {/* Every slice, not the first six. The legend is what names a
                  colour, so a truncated one leaves slices on the chart that
                  nothing identifies — and it wraps, so the length is fine. */}
              <div className="legend">
                {withCounts.map((category, index) => (
                  <span key={category.field_name}>
                    <i
                      style={{ background: CAT_COLORS[index % CAT_COLORS.length] as string }}
                    />
                    {category.label}{' '}
                    <b style={{ color: 'var(--ink)', fontWeight: 600 }}>{n(category.count)}</b>{' '}
                    <span style={{ color: 'var(--ink-45)' }}>{pct(category.share_pct)}</span>
                  </span>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="panel">
          <div className="p-hd">
            <h3>Complaint categories</h3>
            <span className="hint">Largest first</span>
          </div>
          {complaints === undefined ? (
            <div className="p-bd" aria-busy="true">
              <Skeleton height={180} />
            </div>
          ) : withCounts.length === 0 ? (
            <Empty note="Nothing of any category has gone wrong." />
          ) : (
            <div className="chart-wrap">
              <BarChart
                caption="Complaints by category"
                color="var(--amber)"
                rows={withCounts.map((category) => ({
                  label: category.label,
                  value: category.count,
                  note: 'Complaints',
                  meta: pct(category.share_pct),
                }))}
              />
            </div>
          )}
        </div>
      </div>

      {/* Full width, using the room the trend panel used to take. These are
          SKU codes against ten bars: at half width the codes were clipped and
          the bars had barely a third of the page to differ across. */}
      <div className="panel">
        <div className="p-hd">
          <h3>Top complaint SKUs</h3>
          <span className="hint">By count, with the stock each one holds</span>
        </div>
        {complaints === undefined ? (
          <div className="p-bd" aria-busy="true">
            <Skeleton height={180} />
          </div>
        ) : complaints.top_skus.length === 0 ? (
          <Empty note="No SKU in the sheet carries a complaint." />
        ) : (
          <div className="chart-wrap">
            <BarChart
              caption="Complaints by SKU"
              color="var(--rust)"
              rows={complaints.top_skus.map((row) => ({
                label: row.sku,
                value: row.total_complaints,
                note: 'Complaints',
                meta: `${n(row.total_qty)} in stock`,
              }))}
            />
          </div>
        )}
      </div>

      <div className="panel">
        <div className="p-hd">
          <h3>Every SKU</h3>
          <span className="hint">
            Complaints, categories and sales for every imported SKU, in one table
          </span>
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
    </AnalyticsFrame>
  );
}
