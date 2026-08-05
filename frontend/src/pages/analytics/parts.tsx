/**
 * The repeated pieces of the Analytics page.
 *
 * Three ranking tables, four insight lists and six generated cards would
 * otherwise be seventeen near-identical blocks of markup. Extracted so a change
 * to how a rank or an empty state looks happens once.
 *
 * Every one of these renders nothing but real values. Where a list is empty it
 * says what would have to be true for it to fill, rather than showing a row of
 * dashes that reads as data.
 */

import { Icon } from '../../components/Icon';
import type { IconName } from '../../components/Icon';
import { n, pct } from '../../lib/format';
import type { QuickInsight, RankedSku, SkuStatus } from '../../types/api';
import { STATUS_LABEL, STATUS_TONE } from './status';
import type { Measure } from './status';

export function StatusBadge({ status }: { status: SkuStatus }) {
  return (
    <span className={`badge ${STATUS_TONE[status]}`}>
      <span className={`dot ${STATUS_TONE[status]}`} />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Card({
  label,
  value,
  note,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'warn' | 'bad';
  /** For a card whose value is a SKU code rather than a figure. */
  mono?: boolean;
}) {
  return (
    <div className={['kpi', 'static', tone ?? ''].filter(Boolean).join(' ')}>
      <span className="kpi-lbl">{label}</span>
      <span
        className={mono ? 'kpi-val num' : 'kpi-val'}
        style={mono ? { fontSize: 17 } : undefined}
      >
        {value}
      </span>
      <span className="kpi-delta">{note}</span>
    </div>
  );
}

/** A named figure inside a panel — the "Highest selling SKU" style readouts. */
export function Empty({ note }: { note: string }) {
  return (
    <div className="p-bd">
      <div className="hint">{note}</div>
    </div>
  );
}

/**
 * A ranking table. `measure` picks the two columns that follow the SKU, so the
 * three tables the design asks for are one component with three configurations
 * rather than three tables that could drift apart.
 */
export function RankingTable({
  rows,
  measure,
  emptyNote,
}: {
  rows: RankedSku[];
  measure: Measure;
  emptyNote: string;
}) {
  if (rows.length === 0) return <Empty note={emptyNote} />;

  return (
    <div className="tbl-scroll">
      <table className="tbl">
        <thead>
          <tr>
            <th className="n" style={{ width: 44 }}>
              Rank
            </th>
            <th>SKU</th>
            {measure === 'complaints' ? (
              <>
                <th className="n">Total Complaints</th>
              </>
            ) : measure === 'stock' ? (
              <>
                <th className="n">Total Qty</th>
                <th className="n">Shopify Sales</th>
              </>
            ) : (
              <>
                <th className="n">Shopify Sales</th>
                <th className="n">Shopify Sales %</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.sku_normalized}>
              <td className="n">{row.rank}</td>
              <td>
                <span className="num">{row.sku}</span>
              </td>
              {measure === 'complaints' ? (
                <>
                  <td className="n">{n(row.total_complaints)}</td>
                </>
              ) : measure === 'stock' ? (
                <>
                  <td className="n">{n(row.total_qty)}</td>
                  <td className="n">{n(row.shopify_sales)}</td>
                </>
              ) : (
                <>
                  <td className="n">{n(row.shopify_sales)}</td>
                  <td className="n">{pct(row.shopify_sales_pct)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The icons the server may name, mapped to the set the app actually ships. */
const INSIGHT_ICONS: Record<string, IconName> = {
  check: 'check',
  warn: 'warn',
  chart: 'chart',
  box: 'box',
  bell: 'bell',
  x: 'x',
};

/** Cards whose finding is a problem, so the tone matches what it says. */
const ALARMING = new Set(['complaints', 'restock', 'nosales']);

export function InsightCard({ insight }: { insight: QuickInsight }) {
  const tone = ALARMING.has(insight.key) ? 'amber' : 'moss';

  return (
    <div className="kpi static insight">
      <span className="kpi-lbl">
        <Icon name={INSIGHT_ICONS[insight.icon] ?? 'layers'} size="s" /> {insight.title}
      </span>
      {/* The SKU is the headline: the card exists to name one.
          `title` carries it whole, because two lines and an ellipsis is a
          reading aid and must not be the only copy of the identifier. */}
      <span className="insight-sku num" title={insight.sku ?? undefined}>
        {insight.sku ?? '—'}
      </span>
      {/* The badge on its own line. It used to share one with the note, which
          left the figure starting at a different point on every card. */}
      <span className="insight-metric">
        <span className={`badge ${tone}`}>{insight.value}</span>
      </span>
      <span className="insight-note" title={insight.note}>
        {insight.note}
      </span>
    </div>
  );
}
