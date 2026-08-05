/**
 * The seventeen columns of the SKU table, defined once.
 *
 * The Dashboard and SKU Performance show the same row, so they read the same
 * list rather than each keeping their own copy. Two hand-maintained column
 * arrays would agree on the day they were written and drift by the next change,
 * and a figure that appears in a different place on two pages is worse than one
 * that appears on neither.
 */

/**
 * The summary figures. `key` is the server's sort key, so a header can be made
 * clickable wherever sorting is offered.
 *
 * Shopify Sales and Shopify Sales % move with the selected date range; the
 * sheet's own columns do not.
 */
export const SUMMARY_COLUMNS: { key: string; label: string; numeric: boolean }[] = [
  { key: 'sku', label: 'SKU', numeric: false },
  { key: 'total_complaints', label: 'Complaints', numeric: true },
  { key: 'shopify_sales', label: 'Shopify Sales', numeric: true },
  { key: 'shopify_sales_pct', label: 'Shopify Sales %', numeric: true },
  { key: 'total_qty', label: 'Total Quantity', numeric: true },
  { key: 'total_orders', label: 'Total Orders', numeric: true },
];

/**
 * The complaint categories, in the order the table shows them.
 *
 * Ordered by hand rather than taken from the server's `complaint_columns`,
 * because the reading order asked for is not the sheet's own: the four delivery
 * faults first, then defect, damage and electronics as partial/complete pairs.
 * All ten are here, so the Complaints column can always be reconciled against
 * the categories beside it.
 */
export const CATEGORY_COLUMNS: { field: string; label: string }[] = [
  { field: 'missing', label: 'Missing' },
  { field: 'missing_part', label: 'Missing Part' },
  { field: 'item_mismatch_wrong_item', label: 'Wrong Item Delivered' },
  { field: 'order_wrong_parcel', label: 'Order Wrong Parcel' },
  { field: 'item_defect_partial', label: 'Item Defect Partial' },
  { field: 'item_defect_complete', label: 'Item Defect Complete' },
  { field: 'item_damage_partial', label: 'Item Damage Partial' },
  { field: 'item_damage_complete', label: 'Item Damage Complete' },
  { field: 'electronics_nonworking_partial', label: 'Electronics Nonworking Partial' },
  { field: 'electronics_nonworking_complete', label: 'Electronics Nonworking Complete' },
];

/** How the table is ordered wherever it is shown: worst first. */
export const DEFAULT_SORT = 'total_complaints';
export const DEFAULT_DESCENDING = true;

/** How many rows either page shows, ever. */
export const TOP_SKUS = 50;
