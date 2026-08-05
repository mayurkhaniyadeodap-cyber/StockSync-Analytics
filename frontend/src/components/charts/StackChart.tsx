/**
 * 100% stacked bars — composition within each row.
 *
 * Ported from `stackChart` in prototype/strata-prototype.html. Normalised to
 * full width per row on purpose: the question this answers is "how healthy is
 * this category", which is a proportion, and a category with 400 SKUs would
 * otherwise dwarf one with 40 and hide that the small one is mostly out.
 */

import { pct, sharePct } from '../../lib/format';
import { useChartTooltip } from '../../hooks/useChartTooltip';

const W = 720;
const ROW_H = 38;
const LW = 132;
const RW = 150;
/** A part with a nonzero count always draws at least this wide, so it is visible. */
const MIN_PART = 4;

export interface StackPart {
  label: string;
  value: number;
  color: string;
}

export interface StackGroup {
  label: string;
  parts: StackPart[];
}

export interface StackChartProps {
  groups: StackGroup[];
  caption: string;
  /** Right-hand summary for a row. Defaults to "N in · N low · N out". */
  summary?: (group: StackGroup) => string;
}

function defaultSummary(group: StackGroup): string {
  return group.parts.map((p) => `${String(p.value)} ${p.label.toLowerCase()}`).join(' · ');
}

export function StackChart({ groups, caption, summary = defaultSummary }: StackChartProps) {
  const tooltip = useChartTooltip();
  const height = groups.length * ROW_H + 14;
  const track = W - LW - RW;

  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={caption}
    >
      {groups.map((group, gi) => {
        const y = 7 + gi * ROW_H;
        const total = group.parts.reduce((sum, p) => sum + p.value, 0) || 1;
        let x = LW;
        return (
          <g key={group.label}>
            <text x={0} y={y + 20} fontSize={12} fill="var(--ink)">
              {group.label.length > 18 ? `${group.label.slice(0, 17)}…` : group.label}
            </text>
            {group.parts.map((part) => {
              const width = part.value ? Math.max(MIN_PART, (track * part.value) / total) : 0;
              const left = x;
              x += width;
              return width ? (
                <rect
                  key={part.label}
                  x={left}
                  y={y + 8}
                  width={width}
                  height={17}
                  fill={part.color}
                  opacity={0.85}
                  onMouseMove={(event) =>
                    tooltip.show(
                      event,
                      <>
                        <b>
                          {group.label} — {part.label}
                        </b>
                        <div style={{ marginTop: 3 }}>
                          <b>{part.value}</b> SKUs · {pct(sharePct(part.value, total))} of
                          category
                        </div>
                      </>,
                    )
                  }
                  onMouseLeave={tooltip.hide}
                />
              ) : null;
            })}
            <text
              x={W}
              y={y + 21}
              textAnchor="end"
              fontSize={11}
              fontFamily="var(--f-mono)"
              fill="var(--ink-45)"
            >
              {summary(group)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
