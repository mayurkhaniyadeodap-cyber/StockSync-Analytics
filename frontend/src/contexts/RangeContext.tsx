/**
 * The window every figure in the application is read over.
 *
 * It lives above the pages so that moving from the Dashboard to Sales Analytics
 * keeps the window you chose, rather than resetting it on every navigation.
 * This replaced a module-level variable that did the same job for the Analytics
 * pages alone and which the Dashboard could not see.
 *
 * **The provider is optional.** `useSharedRange` falls back to local state when
 * there is none, so a page rendered on its own — in a test, or inside a future
 * embed — still has a working range control rather than a dead one. That is why
 * the context's default is `null` and not a value: a default value would make
 * "no provider" indistinguishable from "provider set to 30", and the fallback
 * would silently never engage.
 */

import { createContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import type { Range } from '../components/charts/RangePicker';

export interface SharedRangeValue {
  days: Range;
  setDays: (days: Range) => void;
}

export type SharedRange = SharedRangeValue;

/** Matches every page's previous local default, so nothing moved by adopting it. */
export const DEFAULT_RANGE_DAYS: Range = 30;

export const RangeContext = createContext<SharedRange | null>(null);

export function RangeProvider({ children }: { children: ReactNode }) {
  const [days, setDays] = useState<Range>(DEFAULT_RANGE_DAYS);
  const value = useMemo<SharedRange>(() => ({ days, setDays }), [days]);

  return <RangeContext.Provider value={value}>{children}</RangeContext.Provider>;
}
