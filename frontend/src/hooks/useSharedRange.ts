/**
 * The range the header shows, or a local one when there is no header.
 *
 * Its own module because a file exporting both a component and a hook loses
 * fast refresh, and `RangeContext.tsx` exports the provider.
 */

import { useContext, useMemo, useState } from 'react';

import type { Range } from '../components/charts/RangePicker';
import { DEFAULT_RANGE_DAYS, RangeContext } from '../contexts/RangeContext';
import type { SharedRange } from '../contexts/RangeContext';

export function useSharedRange(): SharedRange {
  const shared = useContext(RangeContext);
  // Called unconditionally — the hook order must not depend on whether a
  // provider happens to be mounted. The state is simply ignored when one is.
  const [days, setDays] = useState<Range>(DEFAULT_RANGE_DAYS);
  const fallback = useMemo<SharedRange>(() => ({ days, setDays }), [days]);

  return shared ?? fallback;
}
