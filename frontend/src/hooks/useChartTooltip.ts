import { useContext } from 'react';

import { ChartTooltipContext } from '../contexts/ChartTooltipContext';
import type { ChartTooltipValue } from '../contexts/ChartTooltipContext';

/**
 * Outside a provider this is inert rather than an error: a chart rendered on
 * its own — in a test, or embedded in a future export preview — should still
 * draw, just without hover detail.
 */
const INERT: ChartTooltipValue = { show: () => {}, hide: () => {} };

export function useChartTooltip(): ChartTooltipValue {
  return useContext(ChartTooltipContext) ?? INERT;
}
