/**
 * A summary card: glyph, label, figure, and one line under it.
 *
 * The line is either a **trend** — a real change against the period before this
 * one — or a **note**, which is prose describing what the figure counts.
 *
 * Those are two different things and the card refuses to blur them. The
 * reference design puts "↑ 12% vs. previous 30 days" under all six cards, but
 * the API reports a previous period for the sales trend alone: the sheet is a
 * snapshot, so "Total SKUs last month" is not a number anything holds. Cards
 * that have no prior get the note, and the arrow appears only where there is
 * something real behind it.
 *
 * `direction` exists because the sign does not decide the colour. Complaints
 * falling is good news and rendering that in red because the delta is negative
 * would tell the reader the opposite of what happened.
 */

import { Icon } from './Icon';
import type { IconName } from './Icon';
import type { ChipTone } from './shell/PanelHead';

export interface Trend {
  /** Already formatted, e.g. "12.4%". The card supplies the arrow and sign. */
  value: string;
  /** Did the figure rise or fall? */
  up: boolean;
  /** Is that movement good or bad for this particular measure? */
  good: boolean;
  /** What it is measured against — "vs. previous 30 days". */
  against: string;
}

export interface KpiCardProps {
  label: string;
  value: string;
  icon?: IconName;
  tone?: ChipTone;
  /** Prose under the figure, when there is no prior period to compare with. */
  note?: string;
  trend?: Trend;
  /** Tints the card's top edge, for a figure that itself needs attention. */
  alert?: 'warn' | 'bad';
}

export function KpiCard({
  label,
  value,
  icon,
  tone = 'slate',
  note,
  trend,
  alert,
}: KpiCardProps) {
  return (
    <div className={['kpi', 'static', alert ?? ''].filter(Boolean).join(' ')}>
      <div className="kpi-top">
        {icon ? (
          <span className={`kpi-ico ${tone}`} aria-hidden="true">
            <Icon name={icon} size="s" />
          </span>
        ) : null}
        <span className="kpi-lbl">{label}</span>
      </div>

      <span className="kpi-val">{value}</span>

      {trend ? (
        <>
          <span className={`kpi-trend ${trend.good ? 'good' : 'poor'}`}>
            <Icon
              name="up"
              size="s"
              style={trend.up ? undefined : { transform: 'rotate(180deg)' }}
            />
            {trend.value}
          </span>
          <span className="kpi-delta">{trend.against}</span>
        </>
      ) : null}

      {note ? <span className="kpi-delta">{note}</span> : null}
    </div>
  );
}
