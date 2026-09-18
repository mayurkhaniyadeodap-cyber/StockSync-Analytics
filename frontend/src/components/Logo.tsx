/**
 * The product mark.
 *
 * One component rather than the generic `layers` glyph each surface reached for
 * on its own. That glyph is in the icon set for use *as* an icon — beside a nav
 * label, inside a button — and borrowing it as a logo meant the brand changed
 * weight and colour depending on which file drew it.
 *
 * The drawing keeps the product's own motif: strata. Three ascending slabs read
 * as both stock held and a figure rising, which is the whole product in one
 * shape, and they stay legible down to the 16px a browser tab gives them.
 *
 * The tile is part of the mark, not decoration around it: on the navy rail, on
 * white, and on the sign-in card the symbol has to sit on the same blue or it
 * reads as three different logos.
 */

export interface LogoProps {
  /** Tile edge in pixels. The glyph scales with it. */
  size?: number;
  /** Adds "StockSync / Analytics" beside the tile. */
  wordmark?: boolean;
  /** Wordmark colours, for the navy rail where the defaults would vanish. */
  tone?: 'default' | 'onDark';
  className?: string;
}

/** The symbol alone, in a 32-unit box. Used by the tile and by the favicon. */
export function LogoGlyph({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="6" y="19" width="5.2" height="8" rx="1.6" fill="currentColor" opacity="0.55" />
      <rect
        x="13.4"
        y="14"
        width="5.2"
        height="13"
        rx="1.6"
        fill="currentColor"
        opacity="0.8"
      />
      <rect x="20.8" y="7" width="5.2" height="20" rx="1.6" fill="currentColor" />
    </svg>
  );
}

export function Logo({ size = 32, wordmark = false, tone = 'default', className }: LogoProps) {
  const tile = (
    <span
      className="logo-tile"
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.28) }}
    >
      <LogoGlyph size={Math.round(size * 0.92)} />
    </span>
  );

  if (!wordmark) {
    return (
      <span className={['logo', className].filter(Boolean).join(' ')} aria-hidden="true">
        {tile}
      </span>
    );
  }

  return (
    <span
      className={['logo', 'logo-lockup', tone === 'onDark' ? 'on-dark' : '', className]
        .filter(Boolean)
        .join(' ')}
      aria-hidden="true"
    >
      {tile}
      <span className="logo-txt">
        <b>StockSync</b>
        <small>Analytics</small>
      </span>
    </span>
  );
}
