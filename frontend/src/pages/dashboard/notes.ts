/**
 * What each dashboard chart does and does not cover.
 *
 * A plain module rather than exports on `panels.tsx`: a file that exports both a
 * component and a function loses fast refresh, which is the same reason
 * `status.ts` and `dateRange.ts` exist beside their components.
 *
 * These used to sit open under every chart. They are worth keeping — each one
 * stops a real misreading — but three of them along a row of three panels made
 * the panels three different heights and buried the charts under prose. They
 * are now one click away in each panel's header.
 */

import { n } from '../../lib/format';
import type { AnalyticsInsights, Kpis } from '../../types/api';
import type { StockSplit } from './useDashboardPanels';

/** The caveat behind Inventory Health's disclosure. */
export function inventoryHealthNote(kpis: Kpis | undefined, stock: StockSplit): string | null {
  if (!kpis) return null;
  const unread = stock.outOfStock === null || stock.stockedNoSales === null;
  const basis =
    `Low stock is at or below ${n(kpis.low_stock_threshold)} units. ` +
    // The two halves of this donut are not measured the same way, and a reader
    // comparing slices deserves to know which one the range moves.
    'Stock levels come from your most recent import and do not move with the ' +
    'date range; whether a stocked SKU counts as selling is decided by its ' +
    'sales within the range, so those two slices do move.';
  return unread
    ? `${basis} One count could not be read, so those SKUs are left out rather than counted as healthy.`
    : basis;
}

/** The caveat behind Complaint Breakdown's disclosure. */
export function complaintBreakdownNote(insights: AnalyticsInsights | null): string | null {
  if (!insights) return null;
  return (
    `${n(insights.complaints.skus_with_complaints)} of ${n(insights.kpis.total_skus)} SKUs ` +
    'carry at least one complaint. The sheet records totals per SKU without dates, so these ' +
    'are a mix rather than a trend.'
  );
}
