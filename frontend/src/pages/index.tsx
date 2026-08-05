/**
 * One export per interior route.
 *
 * Every screen is real as of M6; App.tsx's import list stays one entry per
 * route regardless of which milestone built it.
 */

export { ImportPage } from './ImportPage';
export { ImportHistoryPage } from './ImportHistoryPage';
export { ShopifyPage } from './ShopifyPage';
export { SyncHistoryPage } from './SyncHistoryPage';

export { DashboardPage } from './DashboardPage';

/**
 * Analytics is five focused pages rather than one. The overview is the section's
 * landing page; the other four are its sub-routes.
 */
export { OverviewPage as AnalyticsPage } from './analytics/OverviewPage';
export { SalesPage } from './analytics/SalesPage';
export { ComplaintsPage } from './analytics/ComplaintsPage';
export { InventoryPage } from './analytics/InventoryPage';
export { PerformancePage } from './analytics/PerformancePage';
export { ReportsPage } from './ReportsPage';
