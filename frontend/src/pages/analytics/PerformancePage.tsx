/**
 * SKU Performance — every SKU, filtered, sorted and exportable.
 *
 * The only page here that does not read `/analytics/insights`: it has its own
 * endpoint because the filters and the sort belong on the server, where the whole
 * set is, rather than on whatever page happens to be loaded.
 *
 * Export goes to `/analytics/performance/export` with the *same* query string, so
 * a download is always the rows on screen — the whole filtered set, not the page.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { ComplaintScopeNote } from '../../components/ComplaintScopeNote';
import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { Page } from '../../components/shell/Page';
import { PageHeader } from '../../components/shell/PageHeader';
import { useToast } from '../../hooks/useToast';
import { API_BASE, StockSyncApiError, api, ensureSession } from '../../lib/api';
import { n } from '../../lib/format';
import type { ComplaintColumn, PerformancePage as Page_, SkuStatus } from '../../types/api';
import { SkuTable } from './SkuTable';
import { DateRangeFilter } from './DateRangeFilter';
import { DEFAULT_RANGE, rangeLabel, rangeParams } from './dateRange';
import type { DateRange } from './dateRange';
import { DEFAULT_DESCENDING, DEFAULT_SORT, TOP_SKUS } from './skuColumns';
import { STATUS_LABEL } from './status';

const STATUSES: SkuStatus[] = ['excellent', 'good', 'attention', 'critical'];

interface FilterState {
  search: string;
  category: string;
  minSalesPct: string;
  minQty: string;
  status: string;
}

const NO_FILTERS: FilterState = {
  search: '',
  category: '',
  minSalesPct: '',
  minQty: '',
  status: '',
};

/** Only the filters actually set become query parameters. */
function toQuery(filters: FilterState): URLSearchParams {
  const params = new URLSearchParams();
  const pairs: [string, string][] = [
    ['search', filters.search.trim()],
    ['complaint_category', filters.category],
    ['min_sales_pct', filters.minSalesPct],
    ['min_qty', filters.minQty],
    ['status', filters.status],
  ];
  for (const [key, value] of pairs) if (value !== '') params.set(key, value);
  return params;
}

export function PerformancePage() {
  const { toast } = useToast();

  const [range, setRange] = useState<DateRange>(DEFAULT_RANGE);
  const [filters, setFilters] = useState<FilterState>(NO_FILTERS);
  const [applied, setApplied] = useState<FilterState>(NO_FILTERS);
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [descending, setDescending] = useState(DEFAULT_DESCENDING);
  const [table, setTable] = useState<Page_ | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [columns, setColumns] = useState<ComplaintColumn[]>([]);

  /** The query the table and its export share, minus paging. */
  /** True when the rows on screen are a search or filter result, not the top 50. */
  const filtered = useMemo(
    () => Object.values(applied).some((value) => value !== ''),
    [applied],
  );

  const query = useMemo(() => {
    const params = toQuery(applied);
    // One window for the whole response. Everything read from Shopify — units
    // sold, and both percentage denominators — is computed over it together,
    // server-side, so a row's parts are always comparable with each other.
    for (const [key, value] of rangeParams(range)) params.set(key, value);
    params.set('sort', sort);
    params.set('descending', String(descending));
    return params;
  }, [applied, descending, range, sort]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const params = new URLSearchParams(query);
      params.set('limit', String(TOP_SKUS));
      const page = await api.get<Page_>(`/analytics/performance?${params.toString()}`);
      setTable(page);
      // Held separately so the filter's category list survives a page that
      // happens to return no rows.
      if (page.complaint_columns.length > 0) setColumns(page.complaint_columns);
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError
          ? caught.message
          : 'Could not load the performance table.',
      );
    }
  }, [query]);

  useEffect(() => {
    setTable(null);
    void load();
  }, [load]);

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setApplied(filters), 300);
    return () => clearTimeout(timer);
  }, [filters]);

  const set = (key: keyof FilterState) => (value: string) =>
    setFilters((current) => ({ ...current, [key]: value }));

  const active = useMemo(
    () => Object.values(filters).filter((value) => value !== '').length,
    [filters],
  );

  /**
   * A plain navigation rather than a fetch: the browser handles the file, the
   * filename comes from Content-Disposition, and the session cookie rides along
   * because it is same-origin.
   */
  const download = async (fmt: 'csv' | 'xlsx') => {
    // A navigation gets no second chance at a 401 the way a fetch does, so the
    // session is renewed first if it is close to expiring.
    if (!(await ensureSession())) return;
    const params = new URLSearchParams(query);
    params.set('format', fmt);
    window.location.assign(`${API_BASE}/analytics/performance/export?${params.toString()}`);
    toast(`Preparing ${fmt.toUpperCase()} of ${n(table?.total ?? 0)} SKUs…`, 'slate');
  };

  return (
    <Page>
      <PageHeader
        title="SKU Performance"
        subtitle={`Top ${TOP_SKUS} Most Complained SKUs over ${rangeLabel(range)}`}
        actions={
          <>
            <DateRangeFilter value={range} onChange={setRange} />
            <button
              className="btn"
              onClick={() => void download('csv')}
              disabled={!table || table.total === 0}
            >
              <Icon name="dl" size="s" /> CSV
            </button>
            <button
              className="btn"
              onClick={() => void download('xlsx')}
              disabled={!table || table.total === 0}
            >
              <Icon name="dl" size="s" /> Excel
            </button>
          </>
        }
      />

      {/* Which columns the range touches, and which it does not. The percentage
          names its own denominator because it is not the one the KPI cards use:
          this column is a share of the sheet, so it adds up to 100%.
          Complaints are deliberately not listed either way — whether they move
          with the range depends on the file they were imported from, which is
          what ComplaintScopeNote below says when it is worth saying. */}
      <div className="trend-scope">
        <b>Shopify Sales</b> and <b>Shopify Sales %</b> cover {rangeLabel(range)}; the
        percentage is a SKU&rsquo;s share of everything your imported SKUs sold in it, so the
        column adds up to 100%. <b>Total Quantity</b> and <b>Total Orders</b> come from your
        most recent import and do not change with the range.
      </div>

      {/* Only when some complaints cannot answer the range — an aggregated
          sheet has no date column to filter on. */}
      <ComplaintScopeNote scope={table?.complaint_scope} />

      <div className="panel">
        <div className="p-hd">
          <h3>Filters</h3>
          {table ? <span className="hint">{n(table.total)} rows match</span> : null}
          <div className="r">
            {active > 0 ? (
              <button className="btn sm" onClick={() => setFilters(NO_FILTERS)}>
                <Icon name="x" size="s" /> Clear {active} filter{active === 1 ? '' : 's'}
              </button>
            ) : null}
          </div>
        </div>
        <div className="p-bd">
          <div className="filters">
            <div className="search" style={{ minWidth: 170 }}>
              <Icon name="search" size="s" />
              <input
                className="inp"
                aria-label="Search SKU"
                placeholder="Search SKU"
                value={filters.search}
                onChange={(event) => set('search')(event.target.value)}
                style={{ paddingLeft: 32 }}
              />
            </div>

            <select
              className="inp"
              aria-label="Complaint category"
              value={filters.category}
              onChange={(event) => set('category')(event.target.value)}
            >
              <option value="">Any complaint category</option>
              {/* From the server with the page, so this list cannot name a
                  category the filter would reject. */}
              {columns.map((column) => (
                <option key={column.field} value={column.field}>
                  {column.header}
                </option>
              ))}
            </select>

            <input
              className="inp"
              type="number"
              min={0}
              max={100}
              step={0.5}
              aria-label="Minimum Shopify sales percent"
              placeholder="Sales % ≥"
              value={filters.minSalesPct}
              onChange={(event) => set('minSalesPct')(event.target.value)}
            />
            <input
              className="inp"
              type="number"
              min={0}
              aria-label="Minimum quantity"
              placeholder="Qty ≥"
              value={filters.minQty}
              onChange={(event) => set('minQty')(event.target.value)}
            />

            <select
              className="inp"
              aria-label="Status"
              value={filters.status}
              onChange={(event) => set('status')(event.target.value)}
            >
              <option value="">Any status</option>
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABEL[status]}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="panel">
        {error ? (
          <div className="p-bd">
            <div className="inline-err">
              <Icon name="warn" />
              <div>{error}</div>
              <button className="btn sm" onClick={() => void load()}>
                Retry
              </button>
            </div>
          </div>
        ) : table === null ? (
          <div className="p-bd" aria-busy="true">
            {[0, 1, 2, 3, 4, 5].map((row) => (
              <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
            ))}
          </div>
        ) : table.rows.length === 0 ? (
          <div className="empty">
            <div className="ei">
              <Icon name="filter" size="l" />
            </div>
            <h3>Nothing matches</h3>
            <p>No SKU fits every filter you have set over {rangeLabel(range)}.</p>
            <div className="acts">
              <button className="btn pri" onClick={() => setFilters(NO_FILTERS)}>
                Clear filters
              </button>
            </div>
          </div>
        ) : (
          <SkuTable
            rows={table.rows}
            sort={sort}
            descending={descending}
            onSort={(key) => {
              // Clicking the active column flips it; a new column starts
              // descending, which is what "show me the worst" means here.
              if (key === sort) setDescending((value) => !value);
              else {
                setSort(key);
                setDescending(true);
              }
            }}
          />
        )}

        {/* No pager: the table is the top 50 and stops there. The count still
            says what was left out — a list that quietly ends at fifty reads as
            a workspace that size — and searching or filtering runs against
            every imported SKU on the server, not against these rows. */}
        {table && table.rows.length > 0 ? (
          <div className="tbl-ft">
            <span>
              {table.total > table.rows.length
                ? filtered
                  ? `Showing ${n(table.rows.length)} of ${n(table.total)} matching SKUs`
                  : `Top ${n(table.rows.length)} most complained of ${n(table.total)} SKUs`
                : `${n(table.total)} SKU${table.total === 1 ? '' : 's'}`}
            </span>
          </div>
        ) : null}
      </div>
    </Page>
  );
}
