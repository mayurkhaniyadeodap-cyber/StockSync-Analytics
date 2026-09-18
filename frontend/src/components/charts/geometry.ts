/**
 * Chart drawing geometry.
 *
 * A plain module rather than constants on `LineChart.tsx`: a file that exports
 * both a component and a value loses fast refresh, which is the same reason
 * `status.ts` and `dateRange.ts` exist beside their components.
 *
 * Two drawings of the same chart. The SVG scales to its container and the font
 * sizes inside are in *its* units, so the viewBox width decides how large the
 * type renders. The wide geometry is what the analytics pages have always used;
 * the narrow one is the same chart at a ratio that stays legible in the
 * dashboard's three-across row, where a panel is about 340px.
 *
 * Which one to use is a choice the caller makes, not a measurement. Reading the
 * container would re-render the chart on every resize for a decision that only
 * has two useful answers.
 */

export interface LineGeometry {
  W: number;
  /** Drawing height. The narrow one is taller relative to its width, because a
   *  third-of-a-row panel has height to spare and not much width. */
  H: number;
  /** Left gutter — a *floor*. See `axisGutter`. */
  L: number;
  R: number;
}

export const LINE_WIDE: LineGeometry = { W: 720, H: 232, L: 54, R: 14 };
export const LINE_NARROW: LineGeometry = { W: 470, H: 290, L: 48, R: 12 };

/**
 * How wide one character is, as a fraction of the font size.
 *
 * The axis renders in `--f-mono`, where every glyph advances by the same
 * amount, so character count times this is the real width rather than a guess.
 * Slightly over the 0.6 the face uses: a pixel generous costs nothing, a pixel
 * short is the bug this exists to prevent.
 */
const MONO_ADVANCE = 0.62;

/** Clear space between the axis labels and the plot. */
const AXIS_GAP = 10;

/**
 * The left gutter the y-axis labels actually need.
 *
 * A constant could not work: the labels are the data's own magnitudes, so a
 * store selling tens of thousands a day gets "80,100" where the gutter was
 * sized for "8,100", and the first character is painted off the left edge of
 * the SVG. That is exactly what happened at 48 units — six digits and a comma
 * need 52 before the gap.
 *
 * The geometry's `L` is a floor, so a chart with small numbers keeps the
 * proportions it was drawn with and only wide labels push the plot right.
 */
export function axisGutter(
  g: LineGeometry,
  labels: readonly string[],
  fontSize: number,
): number {
  const widest = labels.reduce((longest, label) => Math.max(longest, label.length), 0);
  return Math.max(g.L, Math.ceil(widest * MONO_ADVANCE * fontSize) + AXIS_GAP);
}

/** The inner plot width for a given geometry and gutter. */
export const innerWidth = (g: LineGeometry, gutter: number = g.L) => g.W - gutter - g.R;
