/**
 * Payloads shaped like the server's, shared by the five page tests.
 *
 * One module rather than five copies: the pages read different slices of the same
 * response, and five drifting fixtures would let a page pass against a shape the
 * server never sends.
 *
 * **Outside `src/` on purpose.** These are the only invented figures in the
 * project, and an audit asking "does anything ship fake data" should be able to
 * answer it from the directory layout rather than by tracing imports. The eslint
 * config forbids importing this directory from anything that is not a test.
 */

import { vi } from 'vitest';

import type {
  AnalyticsInsights,
  NamedCount,
  PerformancePage,
  PerformanceRow,
  RankedSku,
  SkuStatus,
} from '../../src/types/api';

export type Route = { ok: boolean; status: number; body: unknown };

export function ranked(sku: string, over: Partial<RankedSku> = {}): RankedSku {
  return {
    rank: 1,
    sku,
    sku_normalized: sku.replace(/-/g, '').toLowerCase(),
    shopify_sales: 0,
    shopify_sales_pct: 0,
    total_complaints: 0,
    total_qty: 0,
    total_orders: 0,
    ...over,
  };
}

function count(label: string, value: number, share: number): NamedCount {
  return {
    field_name: label.toLowerCase().replace(/ /g, '_'),
    label,
    count: value,
    share_pct: share,
  };
}

export const COMPLAINT_COLUMNS = [
  { field: 'item_defect_partial', header: 'Item Defect Partial' },
  { field: 'item_damage_partial', header: 'Item Damage Partial' },
];

export const TREND = {
  points: [10, 20, 30].map((units, i) => ({
    day: `2026-07-2${String(i + 6)}`,
    units,
    revenue_paise: 0,
  })),
  previous: [5, 5, 5].map((units, i) => ({
    day: `2026-07-2${String(i + 3)}`,
    units,
    revenue_paise: 0,
  })),
  days: 30,
};

/**
 * Every complaint in these fixtures is dated, so nothing is unfilterable and no
 * note is shown. The undated case has its own fixture below, because that is a
 * different screen rather than a different number.
 */
export const DATED_SCOPE = {
  filtered_by_date: true,
  dated_skus: 4,
  undated_skus: 0,
  undated_complaints: 0,
};

/** An aggregated sheet: no Complaint Date column anywhere in the workspace. */
export const UNDATED_SCOPE = {
  filtered_by_date: false,
  dated_skus: 0,
  undated_skus: 4,
  undated_complaints: 33,
};

/**
 * The common case once a store has imported twice: an aggregated sheet, then a
 * dated export covering part of it. Modelled on the live workspace, where 308
 * SKUs carried dates and 883 did not.
 */
export const MIXED_SCOPE = {
  filtered_by_date: true,
  dated_skus: 308,
  undated_skus: 883,
  undated_complaints: 5456,
};

export const INSIGHTS: AnalyticsInsights = {
  complaint_scope: DATED_SCOPE,
  kpis: {
    total_skus: 4,
    total_qty: 1050,
    shopify_sales: 325,
    shopify_sales_pct: 76.5,
    total_orders: 340,
    total_complaints: 33,
    avg_sales_per_sku: 81.2,
    shopify_sales_all: 425,
  },
  sales: {
    shopify_sales: 325,
    shopify_sales_pct: 76.5,
    highest: ranked('DD-1001', { shopify_sales: 300, shopify_sales_pct: 70.59 }),
    lowest: ranked('DD-1003', { rank: 4 }),
    top: [
      ranked('DD-1001', { rank: 1, shopify_sales: 300, shopify_sales_pct: 70.59 }),
      ranked('DD-1002', { rank: 2, shopify_sales: 20, shopify_sales_pct: 4.71 }),
    ],
    distribution: [count('DD-1001', 300, 92.3), count('DD-1002', 20, 6.2)],
  },
  complaints: {
    total_complaints: 33,
    most_complained: ranked('DD-1002', { total_complaints: 20 }),
    categories: [
      count('Item Defect Partial', 21, 63.6),
      count('Item Damage Partial', 12, 36.4),
    ],
    top_skus: [
      ranked('DD-1002', {
        rank: 1,
        total_complaints: 20,
        shopify_sales: 40,
        total_orders: 40,
      }),
    ],
    skus_with_complaints: 3,
  },
  rankings: {
    top_selling: [ranked('DD-1001', { rank: 1, shopify_sales: 300, shopify_sales_pct: 70.6 })],
    lowest_selling: [ranked('DD-1003', { rank: 1, total_qty: 900 })],
    highest_complaint: [
      ranked('DD-1002', {
        rank: 1,
        total_complaints: 20,
        total_orders: 40,
      }),
    ],
  },
  inventory: {
    high_stock_low_sales: [ranked('DD-1003', { rank: 1, total_qty: 900 })],
    low_stock_high_sales: [ranked('DD-1002', { rank: 1, total_qty: 40, shopify_sales: 20 })],
    zero_sales: [ranked('DD-1003', { rank: 1, total_qty: 900 })],
    most_complaints: [
      ranked('DD-1002', {
        rank: 1,
        total_complaints: 20,
        total_orders: 40,
      }),
    ],
    median_qty: 70,
    median_sales: 12.5,
    zero_sales_total: 1,
  },
  quick: [
    {
      key: 'best',
      icon: 'check',
      title: 'Best performing SKU',
      sku: 'DD-1001',
      value: '300 units',
      note: 'highest sales with no complaints logged',
    },
    {
      key: 'restock',
      icon: 'bell',
      title: 'Restock needed',
      sku: 'DD-1002',
      value: '40 left',
      note: '300 sold in the window',
    },
    {
      key: 'overstocked',
      icon: 'box',
      title: 'Overstocked SKU',
      sku: 'DD-1003',
      value: '900 units held',
      note: 'only 0 sold',
    },
    {
      key: 'nosales',
      icon: 'x',
      title: 'No sales',
      sku: 'DD-1003',
      value: '1 SKUs',
      note: 'holding stock but unsold — DD-1003 has the most at 900',
    },
  ],
  trend: TREND,
  complaint_columns: COMPLAINT_COLUMNS,
  days: 30,
  has_data: true,
  stale: false,
  syncing: false,
  last_computed_at: '2026-07-30T09:00:00Z',
};

function row(
  sku: string,
  status: SkuStatus,
  over: Partial<PerformanceRow> = {},
): PerformanceRow {
  return {
    sku,
    sku_normalized: sku.replace(/-/g, '').toLowerCase(),
    total_count: 120,
    total_qty: 10,
    total_orders: 100,
    shopify_sales: 50,
    shopify_sales_pct: 11.76,
    total_complaints: 0,
    status,
    complaints: {},
    ...over,
  };
}

export const PERFORMANCE: PerformancePage = {
  complaint_scope: DATED_SCOPE,
  rows: [
    row('DD-1001', 'excellent', {
      total_qty: 100,
      shopify_sales: 300,
      shopify_sales_pct: 70.59,
    }),
    row('DD-1002', 'critical', { total_qty: 40, total_complaints: 20 }),
    row('DD-1003', 'attention', { total_qty: 900, shopify_sales: 0, total_orders: 0 }),
  ],
  complaint_columns: COMPLAINT_COLUMNS,
  total: 3,
  limit: 50,
  offset: 0,
  days: 30,
  sort: 'shopify_sales',
  descending: true,
};

export function routes(overrides: Record<string, Route> = {}) {
  const table: Record<string, Route> = {
    'GET /analytics/insights': { ok: true, status: 200, body: INSIGHTS },
    'GET /analytics/performance': { ok: true, status: 200, body: PERFORMANCE },
    'POST /analytics/rebuild': {
      ok: true,
      status: 200,
      body: { rows_written: 12, days_covered: 30, duration_ms: 40 },
    },
    ...overrides,
  };
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const key = Object.keys(table)
      .sort((a, b) => b.length - a.length)
      .find((candidate) => {
        const [routeMethod = '', routePath = ''] = candidate.split(' ');
        return routeMethod === method && url.includes(routePath);
      });
    const route = (key ? table[key] : undefined) ?? { ok: true, status: 200, body: {} };
    return Promise.resolve({
      ok: route.ok,
      status: route.status,
      json: () => Promise.resolve(route.body),
    } as Response);
  });
}

/** An insights response with one branch replaced. */
export function insightsWith(patch: Partial<AnalyticsInsights>) {
  return routes({
    'GET /analytics/insights': { ok: true, status: 200, body: { ...INSIGHTS, ...patch } },
  });
}

/** The URLs a page requested, in order. */
export function requested(fetch: ReturnType<typeof routes>, path: string): string[] {
  return fetch.mock.calls.map((call) => String(call[0])).filter((url) => url.includes(path));
}
