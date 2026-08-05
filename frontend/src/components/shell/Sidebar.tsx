import { NavLink, useLocation } from 'react-router-dom';

import { Icon } from '../Icon';
import type { IconName } from '../Icon';

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** Red dot for the two states the design doc flags as needing attention (§4). */
  alert?: boolean;
  count?: number;
  /**
   * Sub-pages, revealed only while this section is open.
   *
   * Always-visible children would put nine links in one group and bury the rest
   * of the navigation; hiding them behind a click would hide where you already
   * are. Expanding on the active route is the middle path.
   */
  children?: NavItem[];
}

interface NavGroup {
  /** Quiet small-caps section label. Absent for ungrouped top-level items. */
  title?: string;
  items: NavItem[];
}

/**
 * Grouped exactly as design doc §4 specifies. The groups mirror the SDD's own
 * module boundaries, so the navigation teaches the system's structure.
 */
const GROUPS: NavGroup[] = [
  { items: [{ to: '/dashboard', label: 'Dashboard', icon: 'dash' }] },
  {
    title: 'Import',
    items: [
      { to: '/import', label: 'Inventory import', icon: 'import' },
      { to: '/import-history', label: 'Import history', icon: 'clock' },
    ],
  },
  {
    title: 'Shopify',
    items: [
      { to: '/shopify', label: 'Connection', icon: 'plug' },
      { to: '/sync-history', label: 'Sync history', icon: 'sync' },
    ],
  },
  {
    title: 'Insights',
    items: [
      {
        to: '/analytics',
        label: 'Analytics',
        icon: 'chart',
        children: [
          { to: '/analytics/sales', label: 'Sales Analytics', icon: 'chart' },
          { to: '/analytics/complaints', label: 'Complaint Analytics', icon: 'warn' },
          { to: '/analytics/inventory', label: 'Inventory Insights', icon: 'box' },
          { to: '/analytics/performance', label: 'SKU Performance', icon: 'layers' },
        ],
      },
      { to: '/reports', label: 'Reports', icon: 'file' },
    ],
  },
];

/** Its own titled group below the rule, matching the four above it. */
const SETTINGS: NavGroup = {
  title: 'Settings',
  items: [{ to: '/settings', label: 'Settings', icon: 'gear' }],
};

interface SidebarProps {
  /** Icon-only, 72px. Toggled from the sidebar's own footer. */
  collapsed: boolean;
  /** Overlay drawer state, used below 1024px. */
  open: boolean;
  onToggleCollapsed: () => void;
  onNavigate: () => void;
}

export function Sidebar({ collapsed, open, onToggleCollapsed, onNavigate }: SidebarProps) {
  const className = ['side', collapsed ? 'mini' : '', open ? 'open' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <nav className={className} id="side" aria-label="Main navigation">
      <div className="side-nav">
        {GROUPS.map((group, index) => (
          <div key={group.title ?? `group-${index}`}>
            {group.title && <div className="side-grp eyebrow">{group.title}</div>}
            {group.items.map((item) => (
              <SidebarLink key={item.to} item={item} onNavigate={onNavigate} />
            ))}
          </div>
        ))}

        <div style={{ height: 1, background: 'var(--line)', margin: '12px 2px' }} />
        <div>
          <div className="side-grp eyebrow">{SETTINGS.title}</div>
          {SETTINGS.items.map((item) => (
            <SidebarLink key={item.to} item={item} onNavigate={onNavigate} />
          ))}
        </div>
      </div>

      <div className="side-foot">
        <button
          className="nav"
          style={{ height: 32 }}
          onClick={onToggleCollapsed}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          <Icon name={collapsed ? 'right' : 'left'} size="s" />
          <span className="lbl">Collapse</span>
        </button>
      </div>
    </nav>
  );
}

function SidebarLink({
  item,
  onNavigate,
  nested = false,
}: {
  item: NavItem;
  onNavigate: () => void;
  nested?: boolean;
}) {
  const { pathname } = useLocation();
  // Open while anywhere inside the section, so the sub-page you are on is
  // visible in context rather than reachable only by going back to the parent.
  const inSection = pathname === item.to || pathname.startsWith(`${item.to}/`);

  return (
    <>
      <NavLink
        to={item.to}
        end={Boolean(item.children)}
        className={({ isActive }) =>
          [`nav`, isActive ? 'on' : '', nested ? 'sub' : ''].filter(Boolean).join(' ')
        }
        onClick={onNavigate}
        // The label is hidden when collapsed, so the accessible name has to come
        // from somewhere else or the nav becomes unusable to a screen reader.
        title={item.label}
        aria-label={item.label}
      >
        <Icon name={item.icon} />
        <span className="lbl">{item.label}</span>
        {item.count !== undefined && <span className="cnt">{item.count}</span>}
        {item.alert && <span className="alert" aria-label="Needs attention" />}
      </NavLink>

      {item.children && inSection
        ? item.children.map((child) => (
            <SidebarLink key={child.to} item={child} onNavigate={onNavigate} nested />
          ))
        : null}
    </>
  );
}
