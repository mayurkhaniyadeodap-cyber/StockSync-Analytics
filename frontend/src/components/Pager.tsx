/**
 * Paging for the list pages.
 *
 * It exists because those pages were not paged at all: they asked for the
 * endpoint's default fifty rows and rendered them under a footer stating the
 * true total, so a workspace with two hundred imports could see fifty of them
 * and had no way to reach the rest.
 *
 * Numbered rather than just prev/next. The count it reports is the row range —
 * "Showing 51–100 of 214" — because on a reverse-chronological list "page 3"
 * on its own tells you nothing about what you are looking at.
 *
 * Renders nothing when everything fits on one page. A pager that only ever
 * shows a disabled "1" is furniture.
 */

import { Icon } from './Icon';
import { n } from '../lib/format';

export interface PagerProps {
  /** Row offset of the first row on screen. */
  offset: number;
  /** Rows per page. */
  limit: number;
  /** Rows in the whole filtered set, which the endpoints already report. */
  total: number;
  onGo: (offset: number) => void;
  /** What the rows are, for the range sentence: "imports", "syncs". */
  unit?: string;
}

/** At most this many numbered buttons, so the control keeps a fixed width. */
const WINDOW = 5;

export function Pager({ offset, limit, total, onGo, unit = 'rows' }: PagerProps) {
  const pages = Math.ceil(total / limit);
  if (pages <= 1) return null;

  const current = Math.floor(offset / limit);
  // Slides so the current page stays near the middle, and clamps at both ends
  // rather than running past them.
  const first = Math.max(0, Math.min(current - Math.floor(WINDOW / 2), pages - WINDOW));
  const shown = Array.from({ length: Math.min(WINDOW, pages) }, (_, i) => first + i);

  const from = offset + 1;
  const to = Math.min(offset + limit, total);

  return (
    <div className="tbl-ft">
      <span>
        Showing {n(from)}–{n(to)} of {n(total)} {unit}
      </span>
      <span className="spacer" />
      <div className="pager" role="navigation" aria-label="Pagination">
        <button
          className="pg"
          onClick={() => onGo((current - 1) * limit)}
          disabled={current === 0}
          aria-label="Previous page"
        >
          <Icon name="left" size="s" />
        </button>

        {shown.map((page) => (
          <button
            key={page}
            className={`pg${page === current ? ' on' : ''}`}
            onClick={() => onGo(page * limit)}
            aria-label={`Page ${String(page + 1)}`}
            aria-current={page === current ? 'page' : undefined}
          >
            {page + 1}
          </button>
        ))}

        <button
          className="pg"
          onClick={() => onGo((current + 1) * limit)}
          disabled={current >= pages - 1}
          aria-label="Next page"
        >
          <Icon name="right" size="s" />
        </button>
      </div>
    </div>
  );
}
