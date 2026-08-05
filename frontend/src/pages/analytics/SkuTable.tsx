/**
 * The SKU table, rendered the same way wherever it appears.
 *
 * One component rather than the same seventeen `<td>`s written out on two
 * pages: the row is the product of the whole application, and a column that
 * reads differently on the Dashboard from how it reads on SKU Performance would
 * quietly undermine both.
 *
 * Sorting is optional. The Dashboard shows a fixed order and has no headers to
 * click, so it passes no `onSort` and the headers render as plain text — a
 * header that looks clickable and does nothing is worse than a plain one.
 */

import { Icon } from '../../components/Icon';
import { n, pct } from '../../lib/format';
import type { PerformanceRow } from '../../types/api';
import { CATEGORY_COLUMNS, SUMMARY_COLUMNS } from './skuColumns';

export interface SkuTableProps {
  rows: PerformanceRow[];
  /** The active sort key, when the table offers sorting. */
  sort?: string;
  descending?: boolean;
  /** Omit to render fixed headers. */
  onSort?: (key: string) => void;
  maxHeight?: number;
}

export function SkuTable({ rows, sort, descending, onSort, maxHeight = 620 }: SkuTableProps) {
  return (
    <div className="tbl-scroll" style={{ maxHeight }}>
      <table className="tbl sticky-1">
        <thead>
          <tr>
            {SUMMARY_COLUMNS.map((column) => {
              const on = onSort !== undefined && sort === column.key;
              return (
                <th
                  key={column.key}
                  className={column.numeric ? 'n' : undefined}
                  aria-sort={on ? (descending ? 'descending' : 'ascending') : 'none'}
                >
                  {onSort ? (
                    <button className="th-sort" onClick={() => onSort(column.key)}>
                      {column.label}
                      {on ? <Icon name={descending ? 'down' : 'right'} size="s" /> : null}
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              );
            })}
            {CATEGORY_COLUMNS.map((column) => (
              <th key={column.field} className="n no-sort">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.sku_normalized}>
              <td>
                <span className="num">{row.sku}</span>
              </td>
              <td className="n">{n(row.total_complaints)}</td>
              <td className="n">{n(row.shopify_sales)}</td>
              <td className="n">{pct(row.shopify_sales_pct)}</td>
              <td className="n">{n(row.total_qty)}</td>
              <td className="n">{n(row.total_orders)}</td>
              {CATEGORY_COLUMNS.map((column) => (
                <td key={column.field} className="n">
                  {n(row.complaints[column.field] ?? 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
