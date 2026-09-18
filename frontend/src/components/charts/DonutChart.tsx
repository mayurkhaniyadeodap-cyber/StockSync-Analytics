/**
 * Donut chart — share of a whole.
 *
 * Ported from `donutChart` in prototype/strata-prototype.html. Drawn with
 * `stroke-dasharray` on a single circle per slice rather than arc paths: the
 * arithmetic is one circumference multiplication, so there is no trigonometry
 * to get wrong at the wrap-around point.
 */

import { useState } from 'react';

import { n, pct, sharePct } from '../../lib/format';
import { useChartTooltip } from '../../hooks/useChartTooltip';

/*
 * Two drawings, wide and compact.
 *
 * The viewBox decides how large the ring renders, because the SVG scales to its
 * container: a ring of radius 72 centred in a 720-unit box uses a quarter of
 * the width and leaves the rest empty, so in the dashboard's third-of-a-row
 * panel it scaled down to about 80px across.
 *
 * `COMPACT` is square and tight around the ring — same drawing, none of the
 * empty margin — so the same panel renders it at roughly the panel's full
 * width. `WIDE` is unchanged, for the pages where the donut has a half-width
 * panel to sit in.
 */
const WIDE = { W: 720, H: 232, R: 72, SW: 26, VALUE: 24, LABEL: 10.5 } as const;
const COMPACT = { W: 236, H: 236, R: 88, SW: 30, VALUE: 30, LABEL: 11 } as const;

export interface Slice {
  label: string;
  value: number;
  color: string;
}

export interface DonutChartProps {
  slices: Slice[];
  centerValue: string;
  centerLabel: string;
  caption: string;
  /** Square, tight drawing — for a panel sharing its row with two others. */
  compact?: boolean;
}

export function DonutChart({
  slices,
  centerValue,
  centerLabel,
  caption,
  compact = false,
}: DonutChartProps) {
  const tooltip = useChartTooltip();
  const [active, setActive] = useState<number | null>(null);
  const total = slices.reduce((sum, s) => sum + s.value, 0);

  const { W, H, R, SW, VALUE, LABEL } = compact ? COMPACT : WIDE;
  const CX = W / 2;
  const CY = H / 2;
  const C = 2 * Math.PI * R;

  let offset = 0;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={caption}
    >
      <circle
        cx={CX}
        cy={CY}
        r={R}
        fill="none"
        stroke="var(--line)"
        strokeWidth={SW}
        opacity={0.4}
      />

      {total > 0
        ? slices.map((slice, i) => {
            const length = C * (slice.value / total);
            const dashOffset = -offset;
            offset += length;
            return (
              <circle
                key={slice.label}
                cx={CX}
                cy={CY}
                r={R}
                fill="none"
                stroke={slice.color}
                // The 2px taken off each arc is the gap between slices.
                strokeWidth={active === i ? SW + 5 : SW}
                strokeDasharray={`${(length - 2).toFixed(1)} ${(C - length + 2).toFixed(1)}`}
                strokeDashoffset={dashOffset.toFixed(1)}
                transform={`rotate(-90 ${CX} ${CY})`}
                style={{ transition: 'stroke-width 140ms ease' }}
                onMouseMove={(event) => {
                  setActive(i);
                  tooltip.show(
                    event,
                    <>
                      <b>{slice.label}</b>
                      <div style={{ marginTop: 3 }}>
                        {n(slice.value)} units · <b>{pct(sharePct(slice.value, total))}</b>
                      </div>
                    </>,
                  );
                }}
                onMouseLeave={() => {
                  setActive(null);
                  tooltip.hide();
                }}
              />
            );
          })
        : null}

      <text
        x={CX}
        y={CY + VALUE * 0.1}
        textAnchor="middle"
        fontSize={VALUE}
        fontFamily="var(--f-mono)"
        fontWeight={600}
        fill="var(--ink)"
      >
        {centerValue}
      </text>
      <text
        x={CX}
        y={CY + VALUE * 0.8}
        textAnchor="middle"
        fontSize={LABEL}
        letterSpacing={1.2}
        fill="var(--ink-45)"
      >
        {centerLabel}
      </text>
    </svg>
  );
}
