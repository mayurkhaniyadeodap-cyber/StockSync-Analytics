/**
 * Horizontal bar chart — rankings.
 *
 * Ported from `hbarChart` in prototype/strata-prototype.html. Horizontal rather
 * than vertical because the labels are product names: they read straight across
 * instead of being rotated or truncated to fit under a column.
 */

import { n } from '../../lib/format';
import { useChartTooltip } from '../../hooks/useChartTooltip';

const W = 720;
const ROW_H = 30;
const LW = 228;
/** Room for the value alone, and the least a value plus a second figure gets. */
const RW = 68;
const RW_WITH_META = 132;
/** Longer labels are clipped rather than allowed to run into the bars. */
const MAX_LABEL = 30;

const VALUE_SIZE = 11.5;
const META_SIZE = 11;

/**
 * How wide one character is, as a fraction of the font size.
 *
 * Both right-hand figures render in `--f-mono` (JetBrains Mono), where every
 * glyph advances by the same amount — so character count times this is the
 * exact width, not an estimate. It is what lets each figure be given a real
 * column without measuring the DOM.
 *
 * Slightly over the 0.6 the face actually uses, because being a pixel generous
 * costs nothing and being a pixel short is the bug this replaced.
 */
const MONO_ADVANCE = 0.62;

/** Clear space between the value column and the meta column. */
const COL_GAP = 12;
/** …and between the end of the bar track and the value column. */
const BAR_GAP = 10;

const monoWidth = (chars: number, size: number) => Math.ceil(chars * MONO_ADVANCE * size);

const widest = (values: readonly string[]) =>
  values.reduce((longest, value) => Math.max(longest, value.length), 0);

export interface BarRow {
  label: string;
  value: number;
  /** What the tooltip calls the measure. Defaults to "Units sold". */
  note?: string;
  /**
   * A second figure printed after the value — a share, a rate, whatever makes
   * the count mean something. On screen rather than only in a tooltip, because
   * a number you have to hover for is a number most people never see.
   */
  meta?: string;
  /** Extra tooltip line, e.g. the SKU behind a product name. */
  detail?: string;
}

export interface BarChartProps {
  rows: BarRow[];
  format?: (value: number) => string;
  color?: string;
  caption: string;
  onSelect?: (row: BarRow, index: number) => void;
}

export function BarChart({
  rows,
  format = n,
  color = 'var(--slate)',
  caption,
  onSelect,
}: BarChartProps) {
  const tooltip = useChartTooltip();
  const height = rows.length * ROW_H + 16;
  const max = Math.max(...rows.map((r) => r.value), 1);

  /*
   * Two right-aligned figures need two columns, sized to what is actually in
   * them.
   *
   * They used to share one hard-coded 58px gap, which fitted the share this
   * chart was built for ("38.20%", 40px) and nothing else. The complaints chart
   * passes "40 in stock" — 11 monospace characters, 73px — so the value was
   * painted straight through it, and a larger stock figure made the overlap
   * worse: "12,345 in stock" ran 41px into the count. SVG text has no layout
   * box, so nothing pushed them apart; the reservation *was* the layout.
   *
   * Sized from the widest string present rather than from a constant, so no
   * future caller can pass a longer `meta` and reopen this.
   */
  const hasMeta = rows.some((row) => row.meta);
  const metaW = hasMeta ? monoWidth(widest(rows.map((r) => r.meta ?? '')), META_SIZE) : 0;
  const valueW = monoWidth(widest(rows.map((r) => format(r.value))), VALUE_SIZE);

  // Never narrower than it was, so the charts that already fitted keep the bar
  // length they had; wider only when the content genuinely needs it.
  const rightZone = hasMeta
    ? Math.max(RW_WITH_META, BAR_GAP + valueW + COL_GAP + metaW)
    : Math.max(RW, BAR_GAP + valueW);
  const track = W - LW - rightZone;

  /** Right edges. The gap between the two columns is fixed, so they cannot meet. */
  const metaX = W;
  const valueX = hasMeta ? W - metaW - COL_GAP : W;

  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={caption}
    >
      {rows.map((row, i) => {
        const y = 8 + i * ROW_H;
        const label =
          row.label.length > MAX_LABEL ? `${row.label.slice(0, MAX_LABEL - 1)}…` : row.label;
        return (
          <g key={row.label + String(i)}>
            <text x={0} y={y + 16} fontSize={12} fill="var(--ink)">
              {label}
            </text>
            <rect
              x={LW}
              y={y + 5}
              width={track}
              height={15}
              rx={2}
              fill="var(--line)"
              opacity={0.45}
            />
            <rect
              x={LW}
              y={y + 5}
              width={track * (row.value / max)}
              height={15}
              rx={2}
              fill={color}
              style={onSelect ? { cursor: 'pointer' } : undefined}
              onClick={onSelect ? () => onSelect(row, i) : undefined}
              onMouseMove={(event) =>
                tooltip.show(
                  event,
                  <>
                    <b>{row.label}</b>
                    <div style={{ marginTop: 3 }}>
                      {row.note ?? 'Units sold'}: <b>{format(row.value)}</b>
                      {row.meta ? ` · ${row.meta}` : ''}
                    </div>
                    {row.detail ? <small>{row.detail}</small> : null}
                  </>,
                )
              }
              onMouseLeave={tooltip.hide}
            />
            <text
              x={valueX}
              y={y + 16}
              textAnchor="end"
              fontSize={VALUE_SIZE}
              fontFamily="var(--f-mono)"
              fill="var(--ink-60)"
            >
              {format(row.value)}
            </text>
            {row.meta ? (
              // Quieter than the count: the share explains the number, it is
              // not a second number competing with it.
              <text
                x={metaX}
                y={y + 16}
                textAnchor="end"
                fontSize={META_SIZE}
                fontFamily="var(--f-mono)"
                fill="var(--ink-45)"
              >
                {row.meta}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
