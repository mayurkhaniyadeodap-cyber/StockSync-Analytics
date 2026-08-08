import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import { ErrorBoundary } from './components/ErrorBoundary';
import { Icon } from './components/Icon';
import { Skeleton } from './components/Skeleton';
import { AppShell } from './components/shell/AppShell';
import { AuthProvider } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';
import { useAuth } from './hooks/useAuth';
import { LoginPage } from './pages/LoginPage';
import { SettingsPage } from './pages/SettingsPage';
import {
  AnalyticsPage,
  ComplaintsPage,
  DashboardPage,
  ImportHistoryPage,
  ImportPage,
  ReportsPage,
  InventoryPage,
  PerformancePage,
  SalesPage,
  ShopifyPage,
  SyncHistoryPage,
} from './pages';

/**
 * Blocks a route until the session is known.
 *
 * While `status` is 'checking' this renders a static frame. Redirecting early
 * would bounce an already-signed-in user to the login screen on every reload,
 * because the session lives in an httpOnly cookie the page cannot read without
 * asking the server first.
 */
function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'checking') return <BootScreen />;
  // Could not establish whether the session is good — a 500 from the renewal, a
  // dropped connection, a database too busy to write. **Not a sign-out**, so
  // not the login page: sending someone there discards a session that may be
  // perfectly valid, which is exactly what happened after a sync while the
  // rollup rebuild held SQLite's write lock.
  if (status === 'unreachable') return <UnreachableScreen />;
  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/** The server could not be asked. Reloading is the whole remedy. */
function UnreachableScreen() {
  return (
    <div className="boot" role="alert">
      <Icon name="warn" size="l" style={{ color: 'var(--rust)' }} />
      <p style={{ maxWidth: 380, textAlign: 'center', color: 'var(--ink-60)' }}>
        StockSync Analytics couldn&rsquo;t reach the server to check your session. You have not
        been signed out.
      </p>
      <button className="btn pri" onClick={() => window.location.assign(window.location.href)}>
        Try again
      </button>
    </div>
  );
}

/**
 * The session check — design doc §3.
 *
 * Destination-neutral on purpose: until the server answers, this could resolve
 * to the login card or to the dashboard, so it shows the mark and a shimmer
 * rather than a silhouette of either. Faking one and then rendering the other
 * is the flash that makes an app feel assembled at runtime.
 */
function BootScreen() {
  return (
    <div
      className="boot"
      role="status"
      aria-busy="true"
      aria-label="Loading StockSync Analytics"
    >
      <Icon name="layers" size="l" style={{ color: 'var(--slate)' }} />
      <Skeleton height={3} width={132} radius={999} />
    </div>
  );
}

export default function App() {
  return (
    // Outside the providers, so a throw in one of *them* still lands somewhere
    // rather than on a blank document. It offers no "back to dashboard": if the
    // provider tree is broken, routing inside it goes nowhere useful.
    <ErrorBoundary>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />

              <Route
                element={
                  <RequireAuth>
                    <AppShell />
                  </RequireAuth>
                }
              >
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/import" element={<ImportPage />} />
                <Route path="/import-history" element={<ImportHistoryPage />} />
                <Route path="/shopify" element={<ShopifyPage />} />
                <Route path="/sync-history" element={<SyncHistoryPage />} />
                <Route path="/analytics" element={<AnalyticsPage />} />
                <Route path="/analytics/sales" element={<SalesPage />} />
                <Route path="/analytics/complaints" element={<ComplaintsPage />} />
                <Route path="/analytics/inventory" element={<InventoryPage />} />
                <Route path="/analytics/performance" element={<PerformancePage />} />
                <Route path="/reports" element={<ReportsPage />} />
                {/* :section deep-links the settings sub-nav (design doc §13). */}
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/settings/:section" element={<SettingsPage />} />
              </Route>

              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
