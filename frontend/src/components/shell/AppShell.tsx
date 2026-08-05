import { useCallback, useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';

import { ShopifyStatusProvider } from '../../contexts/ShopifyStatusContext';
import { useAuth } from '../../hooks/useAuth';
import { useTheme } from '../../hooks/useTheme';
import { Header } from './Header';
import { Sidebar } from './Sidebar';

/**
 * The application frame (design doc §2.1).
 *
 * Header and sidebar are fixed; the main content area is the only scrolling
 * region, so navigation stays reachable all day. Below 1024px the sidebar
 * becomes an overlay drawer and the scrim closes it.
 */
export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();

  const { user } = useAuth();
  const { setTheme, setDensity } = useTheme();

  // The server is the source of truth for preferences; localStorage only exists
  // so the first paint is not the wrong theme. Once the user is known, adopt
  // whatever they saved — otherwise signing in on a second machine would show
  // that machine's stale local choice.
  useEffect(() => {
    if (!user) return;
    setTheme(user.preferences.theme);
    setDensity(user.preferences.table_density);
  }, [user, setTheme, setDensity]);

  // Route changes close the mobile drawer; leaving it open would cover the page
  // the user just navigated to.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  const closeNav = useCallback(() => setNavOpen(false), []);

  // Escape closes the drawer, as it closes the header popovers. Without it
  // the only ways out were the scrim and a route change — both pointer
  // gestures, which on a tablet with a keyboard is a trap. A key handler
  // rather than `useOnClickOutside`: the scrim already covers the click, and
  // reusing the hook would mean forwarding a ref through Sidebar for the
  // half of it that is already handled.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeNav();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [navOpen, closeNav]);

  return (
    // Mounted at the frame so the header and the dashboard read one shared
    // answer about Shopify rather than each fetching, and possibly disagreeing.
    <ShopifyStatusProvider>
      <div className="app on">
        <Header onOpenNav={() => setNavOpen(true)} />

        <div className="body">
          <Sidebar
            collapsed={collapsed}
            open={navOpen}
            onToggleCollapsed={() => setCollapsed((c) => !c)}
            onNavigate={closeNav}
          />

          <main className="main">
            <Outlet />
          </main>
        </div>

        <div
          className={`scrim${navOpen ? ' on' : ''}`}
          onClick={closeNav}
          aria-hidden={!navOpen}
        />
      </div>
    </ShopifyStatusProvider>
  );
}
