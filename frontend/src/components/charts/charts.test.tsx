// @vitest-environment jsdom
/**
 * The four chart primitives (design doc §16).
 *
 * These assert the arithmetic and the degenerate cases — an all-zero series, a
 * single point, an empty donut — because those are what silently produce a
 * blank or NaN-filled SVG rather than an error.
 */

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { BarChart } from './BarChart';
import { DonutChart } from './DonutChart';
import { LineChart } from './LineChart';
import { LINE_WIDE, innerWidth } from './geometry';
import { StackChart } from './StackChart';

afterEach(cleanup);

function paths(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('path')).map(
    (node) => node.getAttribute('d') ?? '',
  );
}

describe('LineChart', () => {
  it('describes itself to a screen reader instead of being a blank image', () => {
    render(
      <LineChart
        caption="Units sold per day over the last 7 days"
        labels={['1 Jul', '2 Jul']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [10, 20] }]}
      />,
    );

    expect(screen.getByLabelText('Units sold per day over the last 7 days')).toBeDefined();
  });

  it('draws a flat all-zero series rather than dividing by zero', () => {
    const { container } = render(
      <LineChart
        caption="Nothing sold"
        labels={['1 Jul', '2 Jul', '3 Jul']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [0, 0, 0] }]}
      />,
    );

    expect(paths(container).join(' ')).not.toContain('NaN');
  });

  it('centres a single point instead of collapsing to the left edge', () => {
    const { container } = render(
      <LineChart
        caption="One day"
        labels={['1 Jul']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [5] }]}
      />,
    );

    // Derived from the chart's own geometry rather than restated: this asserts
    // that one point lands in the middle, not that the viewBox is 720 wide.
    expect(paths(container)[0]).toContain(
      `M${(LINE_WIDE.L + innerWidth(LINE_WIDE) / 2).toFixed(1)}`,
    );
  });

  it('closes the area fill back to the baseline', () => {
    const { container } = render(
      <LineChart
        caption="Filled"
        labels={['1 Jul', '2 Jul']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [4, 8], fill: true }]}
      />,
    );

    expect(paths(container).some((d) => d.endsWith('Z'))).toBe(true);
  });

  it('thins the x labels so they cannot overlap', () => {
    const labels = Array.from({ length: 90 }, (_, i) => `d${String(i)}`);
    const { container } = render(
      <LineChart
        caption="90 days"
        labels={labels}
        series={[{ name: 'Units', color: 'var(--slate)', values: labels.map(() => 1) }]}
      />,
    );

    // 5 axis labels + at most 8 date labels.
    expect(container.querySelectorAll('text').length).toBeLessThanOrEqual(13);
  });
});

describe('the axis fits inside the drawing', () => {
  /**
   * Both of these were invisible in the code and plain on screen: the first
   * character of "80,100" painted off the left edge of the SVG, and half of
   * "18 Sept" past the right. Nothing threw — SVG text has no layout box and
   * simply draws where it is told, including outside the viewBox.
   */

  function axisText(container: HTMLElement) {
    return Array.from(container.querySelectorAll('text'));
  }

  it('widens the left gutter for labels that need it', () => {
    const { container } = render(
      <LineChart
        compact
        caption="Big numbers"
        labels={['1 Jul', '2 Jul']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [71_500, 40_000] }]}
      />,
    );

    // Every y-axis label starts at x >= 0, i.e. inside the drawing.
    const yLabels = axisText(container).filter(
      (node) => node.getAttribute('text-anchor') === 'end',
    );
    expect(yLabels.length).toBeGreaterThan(0);
    for (const label of yLabels) {
      const right = Number(label.getAttribute('x'));
      // Monospace, so width is character count times the advance.
      const width = (label.textContent ?? '').length * 0.62 * 12;
      expect(right - width).toBeGreaterThanOrEqual(0);
    }
  });

  it('keeps the narrow gutter when the numbers are small', () => {
    /** The geometry's L is a floor, not a minimum that everything pays. */
    const { container } = render(
      <LineChart
        compact
        caption="Small numbers"
        labels={['1 Jul', '2 Jul']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [3, 5] }]}
      />,
    );

    const first = paths(container)[0] ?? '';
    // 48 is LINE_NARROW.L; a one-character label must not push the plot right.
    expect(first.startsWith('M48')).toBe(true);
  });

  it('anchors the end labels inward so neither is cut off', () => {
    const { container } = render(
      <LineChart
        compact
        caption="Dates"
        labels={['20 Aug', '28 Aug', '5 Sept', '13 Sept', '18 Sept']}
        series={[{ name: 'Units', color: 'var(--slate)', values: [1, 2, 3, 4, 5] }]}
      />,
    );

    const dates = axisText(container).filter(
      (node) =>
        (node.textContent ?? '').includes('Aug') || (node.textContent ?? '').includes('Sept'),
    );
    expect(dates[0]?.getAttribute('text-anchor')).toBe('start');
    expect(dates[dates.length - 1]?.getAttribute('text-anchor')).toBe('end');
  });
});

describe('BarChart', () => {
  it('scales every bar against the largest value', () => {
    const { container } = render(
      <BarChart
        caption="Top SKUs"
        rows={[
          { label: 'A', value: 100 },
          { label: 'B', value: 50 },
        ]}
      />,
    );

    // Two track rects and two value rects; the value bars are the 2nd and 4th.
    const widths = Array.from(container.querySelectorAll('rect')).map((node) =>
      Number(node.getAttribute('width')),
    );
    expect(widths[1]).toBeCloseTo((widths[3] ?? 0) * 2, 1);
  });

  it('truncates a label too long for the gutter', () => {
    render(
      <BarChart
        caption="Top SKUs"
        rows={[
          { label: 'A product name that is far too long to fit the label gutter', value: 1 },
        ]}
      />,
    );

    expect(screen.getByText(/…$/)).toBeDefined();
  });

  /**
   * The two right-hand figures are separate right-anchored SVG texts. SVG has
   * no layout box, so if the space reserved for one is narrower than its
   * content the other is painted straight through it — which is what happened
   * on "Top complaint SKUs": the meta "40 in stock" is 73px of monospace and
   * the reservation was 58px, so the complaint count sat on top of it. Larger
   * stock figures widened the overlap to 41px.
   *
   * These measure the gap rather than eyeball the picture, so the geometry
   * cannot regress silently.
   */
  function columns(container: HTMLElement) {
    const texts = Array.from(container.querySelectorAll('text'));
    const mono = texts.filter((node) => node.getAttribute('font-family') === 'var(--f-mono)');
    return mono.map((node) => ({
      text: node.textContent ?? '',
      right: Number(node.getAttribute('x')),
      size: Number(node.getAttribute('font-size')),
    }));
  }

  /** Where a right-anchored monospace string starts, in user units. */
  function leftEdge(column: { text: string; right: number; size: number }) {
    return column.right - column.text.length * 0.62 * column.size;
  }

  describe('the value and its second figure never overlap', () => {
    const cases: [string, string][] = [
      ['a short share', '38.2%'],
      ['a full share', '100.00%'],
      ['the stock note that broke it', '40 in stock'],
      ['a four-figure stock note', '1,234 in stock'],
      ['a five-figure stock note', '12,345 in stock'],
      ['an absurd one', '9,99,99,999 units in stock'],
    ];

    it.each(cases)('%s', (_name, meta) => {
      const { container } = render(
        <BarChart
          caption="Top complaint SKUs"
          rows={[{ label: 'DD-1', value: 12345, meta }]}
        />,
      );
      const [value, second] = columns(container);

      expect(value?.text).toBe('12,345');
      expect(second?.text).toBe(meta);
      // The meta starts to the right of where the value ends.
      expect(leftEdge(second!)).toBeGreaterThan(value!.right);
    });

    it('sizes the column from the widest row, not the first', () => {
      const { container } = render(
        <BarChart
          caption="Top complaint SKUs"
          rows={[
            { label: 'DD-1', value: 5, meta: '4 in stock' },
            { label: 'DD-2', value: 5, meta: '1,234,567 in stock' },
          ]}
        />,
      );
      const mono = columns(container);
      const longest = mono.find((c) => c.text === '1,234,567 in stock');
      const value = mono.find((c) => c.text === '5');

      expect(leftEdge(longest!)).toBeGreaterThan(value!.right);
    });

    it('keeps the value clear of the bar it follows', () => {
      const { container } = render(
        <BarChart
          caption="Top complaint SKUs"
          rows={[{ label: 'DD-1', value: 12345, meta: '12,345 in stock' }]}
        />,
      );
      const track = container.querySelector('rect');
      const barEnd = Number(track?.getAttribute('x')) + Number(track?.getAttribute('width'));
      const [value] = columns(container);

      expect(leftEdge(value!)).toBeGreaterThan(barEnd);
    });

    it('never lets a figure run off the right edge', () => {
      const { container } = render(
        <BarChart
          caption="Top complaint SKUs"
          rows={[{ label: 'DD-1', value: 12345, meta: '12,345 in stock' }]}
        />,
      );

      for (const column of columns(container)) {
        expect(column.right).toBeLessThanOrEqual(720);
        expect(leftEdge(column)).toBeGreaterThan(0);
      }
    });

    it('holds at any zoom, because the chart is a viewBox', () => {
      /**
       * The whole drawing scales as one unit, so nothing can shift relative to
       * anything else — the overlap was never a zoom problem, and this pins the
       * property that makes that true.
       */
      const { container } = render(
        <BarChart
          caption="Top complaint SKUs"
          rows={[{ label: 'DD-1', value: 1, meta: 'x' }]}
        />,
      );
      const svg = container.querySelector('svg');

      expect(svg?.getAttribute('viewBox')).toMatch(/^0 0 720 /);
      expect(svg?.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
    });
  });

  it('prints a second figure beside the value when one is given', () => {
    /** A share that only exists in a tooltip is one most people never see. */
    render(
      <BarChart
        caption="Complaints by category"
        rows={[{ label: 'Missing', value: 412, meta: '38.2%' }]}
      />,
    );

    expect(screen.getByText('412')).toBeDefined();
    expect(screen.getByText('38.2%')).toBeDefined();
  });

  it('leaves the bars alone when no row carries one', () => {
    /**
     * The extra column is only reserved when something needs it, so every
     * chart that had no second figure keeps the bar length it had.
     */
    const rows = [{ label: 'A', value: 100 }];
    const plain = render(<BarChart caption="Top SKUs" rows={rows} />);
    const width = plain.container.querySelectorAll('rect')[0]?.getAttribute('width');
    cleanup();

    const withMeta = render(
      <BarChart caption="Top SKUs" rows={[{ ...rows[0]!, meta: '10%' }]} />,
    );
    const narrowed = withMeta.container.querySelectorAll('rect')[0]?.getAttribute('width');

    expect(Number(narrowed)).toBeLessThan(Number(width));
  });
});

describe('DonutChart', () => {
  it('gives each slice an arc proportional to its share', () => {
    const { container } = render(
      <DonutChart
        caption="Share by category"
        centerValue="300"
        centerLabel="UNITS SOLD"
        slices={[
          { label: 'Kitchen', value: 200, color: 'var(--slate)' },
          { label: 'Toys', value: 100, color: 'var(--moss)' },
        ]}
      />,
    );

    // The background ring plus one circle per slice.
    const arcs = Array.from(container.querySelectorAll('circle')).slice(1);
    const [first = 0] = (arcs[0]?.getAttribute('stroke-dasharray') ?? '')
      .split(' ')
      .map(Number);
    const [second = 0] = (arcs[1]?.getAttribute('stroke-dasharray') ?? '')
      .split(' ')
      .map(Number);
    expect(first).toBeGreaterThan(second);
  });

  it('draws the empty ring rather than crashing when nothing sold', () => {
    const { container } = render(
      <DonutChart caption="Share" centerValue="0" centerLabel="UNITS SOLD" slices={[]} />,
    );

    expect(container.querySelectorAll('circle').length).toBe(1);
    expect(screen.getByText('UNITS SOLD')).toBeDefined();
  });
});

describe('the compact donut', () => {
  it('is square and tight around the ring, so it fills a narrow panel', () => {
    /**
     * The wide drawing centres a radius-72 ring in a 720-unit box: three
     * quarters of the width is empty margin, so scaled into a third-of-a-row
     * panel the ring came out about 80px across.
     */
    const { container } = render(
      <DonutChart
        compact
        caption="Stock levels"
        centerValue="1,372"
        centerLabel="SKUs"
        slices={[{ label: 'In stock', value: 30, color: 'var(--moss)' }]}
      />,
    );

    const [, , width, height] = (container.querySelector('svg')?.getAttribute('viewBox') ?? '')
      .split(' ')
      .map(Number);
    expect(width).toBe(height);

    const ring = container.querySelector('circle');
    const r = Number(ring?.getAttribute('r'));
    const stroke = Number(ring?.getAttribute('stroke-width'));
    // The ring plus its stroke covers most of the box rather than a corner of it.
    expect((2 * r + stroke) / (width as number)).toBeGreaterThan(0.85);
  });

  it('leaves the wide drawing alone, for the panels that have room', () => {
    const { container } = render(
      <DonutChart
        caption="Complaints"
        centerValue="218"
        centerLabel="complaints"
        slices={[{ label: 'Missing', value: 10, color: 'var(--rust)' }]}
      />,
    );

    expect(container.querySelector('svg')?.getAttribute('viewBox')).toBe('0 0 720 232');
  });
});

describe('StackChart', () => {
  it('normalises each row to full width so categories compare by proportion', () => {
    const { container } = render(
      <StackChart
        caption="Stock health"
        groups={[
          {
            label: 'Kitchen',
            parts: [
              { label: 'In', value: 90, color: 'var(--moss)' },
              { label: 'Out', value: 10, color: 'var(--rust)' },
            ],
          },
          {
            label: 'Toys',
            parts: [
              { label: 'In', value: 9, color: 'var(--moss)' },
              { label: 'Out', value: 1, color: 'var(--rust)' },
            ],
          },
        ]}
      />,
    );

    const widths = Array.from(container.querySelectorAll('rect')).map((node) =>
      Number(node.getAttribute('width')),
    );
    // Same proportions, ten times the count — identical bars.
    expect(widths[0]).toBeCloseTo(widths[2] ?? 0, 1);
  });

  it('keeps a tiny nonzero part visible', () => {
    const { container } = render(
      <StackChart
        caption="Stock health"
        groups={[
          {
            label: 'Kitchen',
            parts: [
              { label: 'In', value: 9999, color: 'var(--moss)' },
              { label: 'Out', value: 1, color: 'var(--rust)' },
            ],
          },
        ]}
      />,
    );

    const widths = Array.from(container.querySelectorAll('rect')).map((node) =>
      Number(node.getAttribute('width')),
    );
    expect(widths[1]).toBeGreaterThanOrEqual(4);
  });

  it('omits a zero part entirely', () => {
    const { container } = render(
      <StackChart
        caption="Stock health"
        groups={[
          {
            label: 'Kitchen',
            parts: [
              { label: 'In', value: 10, color: 'var(--moss)' },
              { label: 'Out', value: 0, color: 'var(--rust)' },
            ],
          },
        ]}
      />,
    );

    expect(container.querySelectorAll('rect').length).toBe(1);
  });
});
