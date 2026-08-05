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

const W = 720;
const H = 232;
const CX = W / 2;
const CY = H / 2;
const R = 72;
const SW = 26;
const C = 2 * Math.PI * R;

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
}

export function DonutChart({ slices, centerValue, centerLabel, caption }: DonutChartProps) {
  const tooltip = useChartTooltip();
  const [active, setActive] = useState<number | null>(null);
  const total = slices.reduce((sum, s) => sum + s.value, 0);

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
        y={CY - 2}
        textAnchor="middle"
        fontSize={24}
        fontFamily="var(--f-mono)"
        fontWeight={600}
        fill="var(--ink)"
      >
        {centerValue}
      </text>
      <text
        x={CX}
        y={CY + 18}
        textAnchor="middle"
        fontSize={10.5}
        letterSpacing={1.2}
        fill="var(--ink-45)"
      >
        {centerLabel}
      </text>
    </svg>
  );
}
