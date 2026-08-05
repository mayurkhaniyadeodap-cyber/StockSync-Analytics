import type { CSSProperties } from 'react';

interface SkeletonProps {
  /** Bare numbers are px. */
  height?: number | string;
  width?: number | string;
  /** Defaults to the 4px of `.sk`; pass 999 for a pill. */
  radius?: number;
  className?: string;
  style?: CSSProperties;
}

/**
 * The shimmer placeholder (design doc §18).
 *
 * A skeleton stands in for content whose *shape* is already known — a row, a
 * KPI value, a chart. It is deliberately not a spinner: a spinner says "wait",
 * a skeleton says "here is what is arriving", which is why the data screens
 * from M2 onward use these rather than a centred spinner.
 *
 * `aria-hidden` throughout: the shimmer is decoration. The container that owns
 * the pending region carries `aria-busy`, so a screen reader is told once that
 * something is loading instead of once per bar.
 */
export function Skeleton({
  height = 12,
  width = '100%',
  radius,
  className,
  style,
}: SkeletonProps) {
  return (
    <span
      className={['sk', className].filter(Boolean).join(' ')}
      aria-hidden="true"
      style={{
        display: 'block',
        height,
        width,
        ...(radius === undefined ? {} : { borderRadius: radius }),
        ...style,
      }}
    />
  );
}
