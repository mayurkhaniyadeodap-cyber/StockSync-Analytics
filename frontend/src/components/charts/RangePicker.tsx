/**
 * The compact per-chart range control (design doc §7.3).
 *
 * Each chart carries its own, rather than one page-wide filter: comparing a
 * 7-day sales spike against a 90-day category mix is a normal thing to want,
 * and a single global range makes it impossible.
 */

export const RANGES = [7, 30, 90] as const;
export type Range = (typeof RANGES)[number];

export interface RangePickerProps {
  value: number;
  onChange: (days: Range) => void;
  label: string;
  options?: readonly Range[];
}

export function RangePicker({ value, onChange, label, options = RANGES }: RangePickerProps) {
  return (
    <div className="seg mono" role="group" aria-label={`${label} range`}>
      {options.map((days) => (
        <button
          key={days}
          type="button"
          className={days === value ? 'on' : ''}
          aria-pressed={days === value}
          onClick={() => onChange(days)}
        >
          {days}D
        </button>
      ))}
    </div>
  );
}
