/**
 * Icon set.
 *
 * Lifted verbatim from the prototype's SVG sprite. Design doc §1.3: simple
 * 1.5px stroke line icons, never filled or duotone — the stroke width and
 * fill:none come from the `.ic` class in components.css, so every icon inherits
 * currentColor and stays visually quiet.
 */

interface IconDef {
  viewBox: string;
  d: string;
}

const ICONS: Record<string, IconDef> = {
  dash: {
    viewBox: '0 0 24 24',
    d: `<rect x="3" y="3" width="7" height="8" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="3" y="15" width="7" height="6" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/>`,
  },
  import: {
    viewBox: '0 0 24 24',
    d: `<path d="M12 3v11m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>`,
  },
  clock: {
    viewBox: '0 0 24 24',
    d: `<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>`,
  },
  plug: {
    viewBox: '0 0 24 24',
    d: `<path d="M10.5 13.5 7 17a3.5 3.5 0 0 1-5-5l3.5-3.5"/><path d="M13.5 10.5 17 7a3.5 3.5 0 0 0-5-5L8.5 5.5"/><path d="M9.5 14.5 15 9"/>`,
  },
  sync: {
    viewBox: '0 0 24 24',
    d: `<path d="M20 11a8 8 0 0 0-14-4.5L3 9"/><path d="M4 13a8 8 0 0 0 14 4.5L21 15"/><path d="M3 5v4h4M21 19v-4h-4"/>`,
  },
  match: { viewBox: '0 0 24 24', d: `<path d="M12 3 20 12l-8 9-8-9z"/><path d="M8.5 12h7"/>` },
  chart: {
    viewBox: '0 0 24 24',
    d: `<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16v-5M13 16V7M18 16v-8"/>`,
  },
  file: {
    viewBox: '0 0 24 24',
    d: `<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>`,
  },
  gear: {
    viewBox: '0 0 24 24',
    d: `<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.1a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11a2 2 0 1 1 0 4z"/>`,
  },
  search: {
    viewBox: '0 0 24 24',
    d: `<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>`,
  },
  bell: {
    viewBox: '0 0 24 24',
    d: `<path d="M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7"/><path d="M13.7 20a2 2 0 0 1-3.4 0"/>`,
  },
  down: { viewBox: '0 0 24 24', d: `<path d="m6 9 6 6 6-6"/>` },
  left: { viewBox: '0 0 24 24', d: `<path d="m14 6-6 6 6 6"/>` },
  right: { viewBox: '0 0 24 24', d: `<path d="m10 6 6 6-6 6"/>` },
  check: { viewBox: '0 0 24 24', d: `<path d="m5 13 4 4L19 7"/>` },
  warn: {
    viewBox: '0 0 24 24',
    d: `<path d="M10.3 4.3 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>`,
  },
  x: { viewBox: '0 0 24 24', d: `<path d="M6 6 18 18M18 6 6 18"/>` },
  // Stroked outline and a pupil, not a filled almond: §1.3 allows no fills, and
  // the crossed-out variant carries the slash the eye alone cannot.
  eye: {
    viewBox: '0 0 24 24',
    d: `<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3.2"/>`,
  },
  'eye-off': {
    viewBox: '0 0 24 24',
    d: `<path d="M9.9 5.8A9.7 9.7 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17.6 17.6 0 0 1-3.3 4.1"/><path d="M6.4 7.8A17.4 17.4 0 0 0 2.5 12S6 18.5 12 18.5a9.5 9.5 0 0 0 3.4-.6"/><path d="M9.8 9.8a3.2 3.2 0 0 0 4.4 4.4"/><path d="m4 4 16 16"/>`,
  },
  sheet: {
    viewBox: '0 0 24 24',
    d: `<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M9 9.5V20M3 15h18"/>`,
  },
  link: {
    viewBox: '0 0 24 24',
    d: `<path d="M9 15 15 9"/><path d="M11 6.5 13 4.5a3.5 3.5 0 1 1 5 5l-2 2"/><path d="M13 17.5 11 19.5a3.5 3.5 0 1 1-5-5l2-2"/>`,
  },
  cloud: {
    viewBox: '0 0 24 24',
    d: `<path d="M7 18a4 4 0 0 1-.4-8A6 6 0 0 1 18 9.5a3.8 3.8 0 0 1 1 7.4"/><path d="M12 21v-8m0 0 3 3m-3-3-3 3"/>`,
  },
  plus: { viewBox: '0 0 24 24', d: `<path d="M12 5v14M5 12h14"/>` },
  dl: { viewBox: '0 0 24 24', d: `<path d="M12 4v11m0 0 4-4m-4 4-4-4"/><path d="M5 19h14"/>` },
  sun: {
    viewBox: '0 0 24 24',
    d: `<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>`,
  },
  moon: {
    viewBox: '0 0 24 24',
    d: `<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"/>`,
  },
  menu: { viewBox: '0 0 24 24', d: `<path d="M4 7h16M4 12h16M4 17h16"/>` },
  user: {
    viewBox: '0 0 24 24',
    d: `<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>`,
  },
  out: {
    viewBox: '0 0 24 24',
    d: `<path d="M15 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h9"/><path d="M18 12H9m9 0-3.5-3.5M18 12l-3.5 3.5"/>`,
  },
  box: {
    viewBox: '0 0 24 24',
    d: `<path d="m3.5 7.5 8.5-4.5 8.5 4.5v9L12 21l-8.5-4.5z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9"/>`,
  },
  layers: {
    viewBox: '0 0 24 24',
    d: `<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>`,
  },
  filter: { viewBox: '0 0 24 24', d: `<path d="M3 5h18l-7 8v6l-4 2v-8z"/>` },
  trash: { viewBox: '0 0 24 24', d: `<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>` },
  refresh: {
    viewBox: '0 0 24 24',
    d: `<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>`,
  },
};

export type IconName = keyof typeof ICONS;

interface IconProps {
  name: IconName;
  /** `s` = 14px, default = 18px, `l` = 22px — matches the prototype. */
  size?: 's' | 'l';
  className?: string;
  style?: React.CSSProperties;
}

export function Icon({ name, size, className, style }: IconProps) {
  const icon = ICONS[name];
  if (!icon) return null;

  const classes = ['ic', size ? size : '', className ?? ''].filter(Boolean).join(' ');

  return (
    <svg
      className={classes}
      viewBox={icon.viewBox}
      style={style}
      aria-hidden="true"
      focusable="false"
      dangerouslySetInnerHTML={{ __html: icon.d }}
    />
  );
}
