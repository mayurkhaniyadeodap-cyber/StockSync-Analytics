/**
 * The period every figure on the SKU Performance page is computed over.
 *
 * Four presets and a custom pair. Kept separate from the chart `RangePicker`
 * deliberately: that control answers "how far back does this chart go" and its
 * presets are shared by every chart on the site, while this one governs a whole
 * table — sales, complaints and both percentage denominators at once. Widening
 * the chart control to carry dates would push a custom range onto pages that
 * have no use for one.
 *
 * A custom range is only reported once *both* bounds are set. A half-given range
 * would otherwise send the table off to a window the user has not finished
 * describing, and the rows would change under their hands mid-edit.
 */

import { useState } from 'react';

import { RANGE_PRESETS, DEFAULT_RANGE, today } from './dateRange';
import type { DateRange } from './dateRange';
import { Icon } from '../../components/Icon';

export interface DateRangeFilterProps {
  value: DateRange;
  onChange: (range: DateRange) => void;
}

export function DateRangeFilter({ value, onChange }: DateRangeFilterProps) {
  const custom = value.kind === 'custom';
  // Held here rather than lifted: while one bound is filled and the other is
  // not, there is no range to report, and the table should not reload.
  const [since, setSince] = useState(custom ? value.since : '');
  const [until, setUntil] = useState(custom ? value.until : today());
  const [open, setOpen] = useState(custom);

  /** Report upward only when the pair is complete and the right way round. */
  const settle = (nextSince: string, nextUntil: string) => {
    setSince(nextSince);
    setUntil(nextUntil);
    if (nextSince !== '' && nextUntil !== '' && nextSince <= nextUntil) {
      onChange({ kind: 'custom', since: nextSince, until: nextUntil });
    }
  };

  const backwards = since !== '' && until !== '' && since > until;

  return (
    <div className="range-filter">
      <div className="seg mono" role="group" aria-label="Date range">
        {RANGE_PRESETS.map((days) => {
          const on = !open && value.kind === 'preset' && value.days === days;
          return (
            <button
              key={days}
              type="button"
              className={on ? 'on' : ''}
              aria-pressed={on}
              onClick={() => {
                setOpen(false);
                onChange({ kind: 'preset', days });
              }}
            >
              {days}D
            </button>
          );
        })}
        <button
          type="button"
          className={open ? 'on' : ''}
          aria-pressed={open}
          onClick={() => {
            const next = !open;
            setOpen(next);
            // Closing the custom editor returns to the last preset rather than
            // leaving the table on a window the control no longer shows.
            if (!next && value.kind === 'custom') onChange(DEFAULT_RANGE);
            else if (next && since !== '' && until !== '') settle(since, until);
          }}
        >
          <Icon name="clock" size="s" /> Custom
        </button>
      </div>

      {open ? (
        <div className="range-custom">
          <input
            className="inp"
            type="date"
            aria-label="Start date"
            max={until || today()}
            value={since}
            onChange={(event) => settle(event.target.value, until)}
          />
          <span className="hint">to</span>
          <input
            className="inp"
            type="date"
            aria-label="End date"
            min={since || undefined}
            max={today()}
            value={until}
            onChange={(event) => settle(since, event.target.value)}
          />
          {backwards ? (
            <span className="hint err" role="status">
              Start date must come first
            </span>
          ) : since === '' || until === '' ? (
            <span className="hint" role="status">
              Pick both dates
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
