/**
 * Line chart — at most two series (design doc §16).
 *
 * Ported from `lineChart` in prototype/strata-prototype.html: same viewBox,
 * same margins, same 5 gridlines, same 0.09-opacity area fill. Drawn as plain
 * SVG rather than pulled from a charting library so it inherits the theme
 * tokens directly and adds nothing to the bundle.
 */

import { useState } from 'react';

import { LINE_NARROW, LINE_WIDE, axisGutter, innerWidth } from './geometry';
import { n } from '../../lib/format';
import { useChartTooltip } from '../../hooks/useChartTooltip';

/*
 * The drawing's own units. The SVG scales to its container, so these decide the
 * *proportions*, and the font sizes below are in these units too — which means
 * a wide viewBox in a narrow panel renders its labels small.
 *
 * 720 was right while every chart had a full-width panel. On the dashboard the
 * trend now shares a three-across row and gets roughly 400px, where 720 units
 * put the axis labels at about six pixels. 560 is the same drawing at a less
 * extreme ratio: the same panel renders them at nine, and a full-width panel is
 * unaffected because it was never the constraint.
 */
const T = 16;
const B = 30;
/** Axis type, in viewBox units. */
const AXIS_SIZE = 12;

/** The inner plot width for a given geometry. */

export interface Series {
  name: string;
  color: string;
  values: number[];
  fill?: boolean;
  dashed?: boolean;
}

export interface LineChartProps {
  labels: string[];
  series: Series[];
  /** Value formatter for the axis and the tooltip. Defaults to plain numbers. */
  format?: (value: number) => string;
  /** Announced to screen readers in place of the drawing. */
  caption: string;
  freshness?: string;
  /** Narrower drawing, for a panel sharing its row with two others. */
  compact?: boolean;
}

export function LineChart({
  labels,
  series,
  format = n,
  caption,
  freshness,
  compact = false,
}: LineChartProps) {
  const tooltip = useChartTooltip();
  const [active, setActive] = useState<number | null>(null);
  const geometry = compact ? LINE_NARROW : LINE_WIDE;
  const { W, H, R } = geometry;
  const IH = H - T - B;

  // A flat all-zero series would collapse the scale to a single line at the
  // bottom and divide by zero; 1 keeps the axis readable and the chart honest.
  const peak = Math.max(...series.flatMap((s) => s.values), 0);
  const max = peak > 0 ? peak * 1.12 : 1;

  // The axis values are known before the gutter is, because the gutter is sized
  // to fit them. Six digits and a comma need more room than four do.
  const ticks = [0, 1, 2, 3, 4].map((i) => format(Math.round(max - (max * i) / 4)));
  const L = axisGutter(geometry, ticks, AXIS_SIZE);
  const IW = innerWidth(geometry, L);

  const x = (i: number) => L + (labels.length === 1 ? IW / 2 : (IW * i) / (labels.length - 1));
  const y = (v: number) => T + IH - (v / max) * IH;
  // Seven dates fit the wide drawing; in the narrow one they collide, so it
  // shows four. The last label is always drawn, so the axis still ends where
  // the data does.
  const step = Math.max(1, Math.ceil(labels.length / (compact ? 4 : 7)));

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={caption}
    >
      {[0, 1, 2, 3, 4].map((i) => {
        const yy = T + (IH * i) / 4;
        return (
          <g key={i}>
            <line x1={L} y1={yy} x2={W - R} y2={yy} stroke="var(--line)" strokeWidth={1} />
            <text
              x={L - 10}
              y={yy + 4}
              textAnchor="end"
              fontSize={AXIS_SIZE}
              fontFamily="var(--f-mono)"
              fill="var(--ink-45)"
            >
              {ticks[i]}
            </text>
          </g>
        );
      })}

      {labels.map((label, i) =>
        i % step === 0 || i === labels.length - 1 ? (
          <text
            key={label + String(i)}
            x={x(i)}
            y={H - 10}
            /* The end labels are anchored to the plot's edges rather than
               centred on them. Centred, half of "18 Sept" hung past the right
               of the viewBox and was cut off — the axis has no margin to spend
               there, and widening one for two labels would shorten the plot. */
            textAnchor={i === 0 ? 'start' : i === labels.length - 1 ? 'end' : 'middle'}
            fontSize={AXIS_SIZE}
            fontFamily="var(--f-mono)"
            fill="var(--ink-45)"
          >
            {label}
          </text>
        ) : null,
      )}

      {series.map((s, si) => {
        const d = s.values
          .map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
          .join(' ');
        return (
          <g key={s.name}>
            {s.fill ? (
              <path
                d={`${d} L${x(s.values.length - 1).toFixed(1)} ${T + IH} L${x(0)} ${T + IH} Z`}
                fill={s.color}
                opacity={0.09}
              />
            ) : null}
            <path
              d={d}
              fill="none"
              stroke={s.color}
              strokeWidth={s.dashed ? 1.5 : 2}
              strokeDasharray={s.dashed ? '4 4' : undefined}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {s.values.map((v, i) => (
              <circle
                key={i}
                cx={x(i)}
                cy={y(v)}
                r={active === i ? 3.5 : 0}
                fill="var(--paper)"
                stroke={s.color}
                strokeWidth={2}
                data-series={si}
              />
            ))}
          </g>
        );
      })}

      {/* Full-height hit strips: hovering anywhere in a column reads that day,
          which is far easier than aiming at a 3.5px point. */}
      {labels.map((label, i) => (
        <rect
          key={`hit-${String(i)}`}
          className="hit"
          x={x(i) - IW / labels.length / 2}
          y={T}
          width={IW / labels.length}
          height={IH}
          fill="transparent"
          onMouseMove={(event) => {
            setActive(i);
            tooltip.show(
              event,
              <>
                <b>{label}</b>
                {series.map((s) => (
                  <div key={s.name} style={{ marginTop: 3 }}>
                    {s.name}: <b>{format(s.values[i] ?? 0)}</b>
                  </div>
                ))}
                {freshness ? <small>{freshness}</small> : null}
              </>,
            );
          }}
          onMouseLeave={() => {
            setActive(null);
            tooltip.hide();
          }}
        />
      ))}
    </svg>
  );
}
