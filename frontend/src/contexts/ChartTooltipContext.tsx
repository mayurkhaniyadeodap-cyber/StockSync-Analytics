/**
 * The shared chart tooltip.
 *
 * One fixed-position node for every chart on the page, rather than one per
 * chart: it follows the cursor across panel boundaries and can never be
 * clipped by a panel's `overflow`. Ported from `.ctip` in the prototype.
 */

import { createContext, useCallback, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export interface ChartTooltipValue {
  /** Show `content` at the pointer. */
  show: (event: { clientX: number; clientY: number }, content: ReactNode) => void;
  hide: () => void;
}

export const ChartTooltipContext = createContext<ChartTooltipValue | null>(null);

interface Position {
  x: number;
  y: number;
  content: ReactNode;
}

export function ChartTooltipProvider({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<Position | null>(null);

  const show = useCallback(
    (event: { clientX: number; clientY: number }, content: ReactNode) => {
      setTip({ x: event.clientX, y: event.clientY, content });
    },
    [],
  );
  const hide = useCallback(() => setTip(null), []);
  // Stable, or every chart on the page re-renders on each mousemove.
  const value = useMemo<ChartTooltipValue>(() => ({ show, hide }), [show, hide]);

  return (
    <ChartTooltipContext.Provider value={value}>
      {children}
      <div
        className={tip ? 'ctip on' : 'ctip'}
        role="presentation"
        style={{ left: tip?.x ?? 0, top: tip?.y ?? 0 }}
      >
        {tip?.content}
      </div>
    </ChartTooltipContext.Provider>
  );
}
