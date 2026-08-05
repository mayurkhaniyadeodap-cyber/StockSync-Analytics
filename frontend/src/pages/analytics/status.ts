/**
 * How a SKU's automatic verdict is spelled and coloured.
 *
 * Its own module rather than sitting beside the components that use it: a file
 * that exports both a component and a constant loses fast refresh, and these are
 * imported by the page, the parts and the tests alike.
 */

import type { SkuStatus, Trend } from '../../types/api';

/** Design tokens, so the badges follow the light/dark switch (design §16). */
export const STATUS_TONE: Record<SkuStatus, string> = {
  excellent: 'moss',
  good: 'slate',
  attention: 'amber',
  critical: 'rust',
};

export const STATUS_LABEL: Record<SkuStatus, string> = {
  excellent: 'Excellent',
  good: 'Good',
  attention: 'Needs attention',
  critical: 'Critical',
};

/** Which pair of measures a ranking table shows beside the SKU. */
export type Measure = 'sales' | 'complaints' | 'stock';

/** X-axis labels for a trend, in the reader's locale rather than ISO. */
export function labelsFor(trend: Trend): string[] {
  return trend.points.map((point) =>
    new Date(`${point.day}T00:00:00`).toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
    }),
  );
}
