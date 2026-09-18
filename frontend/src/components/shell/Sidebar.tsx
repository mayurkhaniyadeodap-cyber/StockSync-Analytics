import { NavLink, useLocation } from 'react-router-dom';

import { Icon } from '../Icon';
import type { IconName } from '../Icon';

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** Red dot for a state that needs attention. */
  alert?: boolean;
  count?: number;
  /** Matched as a prefix too, so a sub-route keeps its parent highlighted. */
  section?: boolean;
}

interface NavGroup {
  /** Quiet small-caps heading. Absent for the ungrouped block at the top. */
  title?: string;
  items: NavItem[];
}

/**
 * The rail, grouped as the reference design groups it.
 *
 * The five operational destinations sit unlabelled at the top — they are the
 * daily path through the product and need no heading to explain them. Below
 * that the headings earn their line, because each one introduces a set rather
 * than a pair.
 *
 * **Every Analytics page is listed.** They used to be children that appeared
 * only while the section was open, which hid four of the twelve pages behind a
 * click and made the rail a poor answer to "what is in this product".
 */
const GROUPS: NavGroup[] = [
  {
    items: [
      { to: '/dashboard', label: 'Dashboard', icon: 'dash' },
      { to: '/import', label: 'Import Data', icon: 'import' },
      { to: '/import-history', label: 'Import History', icon: 'clock' },
      { to: '/shopify', label: 'Shopify Connection', icon: 'plug' },
      { to: '/sync-history', label: 'Sync History', icon: 'sync' },
    ],
  },
  {
    title: 'Analytics',
    items: [
      { to: '/analytics', label: 'Overview', icon: 'dash' },
      { to: '/analytics/sales', label: 'Sales Analytics', icon: 'chart' },
      { to: '/analytics/complaints', label: 'Complaint Analytics', icon: 'warn' },
      { to: '/analytics/inventory', label: 'Inventory Analytics', icon: 'box' },
      { to: '/analytics/performance', label: 'Product Performance', icon: 'layers' },
    ],
  },
  {
    title: 'Reports',
    items: [{ to: '/reports', label: 'Reports', icon: 'file' }],
  },
  {
    title: 'Settings',
    items: [{ to: '/settings', label: 'Settings', icon: 'gear', section: true }],
  },
];

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
          <div key={group.title ?? `group-${String(index)}`}>
            {group.title && <div className="side-grp eyebrow">{group.title}</div>}
            {group.items.map((item) => (
              <SidebarLink key={item.to} item={item} onNavigate={onNavigate} />
            ))}
          </div>
        ))}
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

function SidebarLink({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  const { pathname } = useLocation();

  /*
   * `/analytics` must not light up while `/analytics/sales` is open — they are
   * two entries in the same list now, and two highlighted rows read as two
   * current pages. `end` handles that. Settings is the exception: its sub-routes
   * (`/settings/profile`) are tabs within the one page, so it matches by prefix.
   */
  const active = item.section
    ? pathname === item.to || pathname.startsWith(`${item.to}/`)
    : undefined;

  return (
    <NavLink
      to={item.to}
      end={!item.section}
      className={({ isActive }) =>
        ['nav', (active ?? isActive) ? 'on' : ''].filter(Boolean).join(' ')
      }
      onClick={onNavigate}
      // The label is hidden when collapsed, so the accessible name has to come
      // from somewhere else or the rail becomes unusable to a screen reader.
      title={item.label}
      aria-label={item.label}
    >
      <Icon name={item.icon} />
      <span className="lbl">{item.label}</span>
      {item.count !== undefined && <span className="cnt">{item.count}</span>}
      {item.alert && <span className="alert" aria-label="Needs attention" />}
    </NavLink>
  );
}
