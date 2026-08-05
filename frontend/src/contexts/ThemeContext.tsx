import { createContext, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import type { TableDensity, Theme } from '../types/api';

const THEME_KEY = 'stocksync.theme';
const DENSITY_KEY = 'stocksync.density';

export interface ThemeContextValue {
  theme: Theme;
  density: TableDensity;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  setDensity: (density: TableDensity) => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const stored = localStorage.getItem(key);
    return allowed.includes(stored as T) ? (stored as T) : fallback;
  } catch {
    // Private browsing and blocked storage both throw here. A preference is not
    // worth failing a render over.
    return fallback;
  }
}

/**
 * Theme and table density.
 *
 * These are also persisted server-side per user (design doc §13, §15), but they
 * are mirrored into localStorage so the correct theme is applied on the very
 * first paint — waiting for /auth/me would flash a light screen at a dark-theme
 * user on every reload.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() =>
    readStored(THEME_KEY, ['light', 'dark'] as const, 'light'),
  );
  const [density, setDensityState] = useState<TableDensity>(() =>
    readStored(DENSITY_KEY, ['comfortable', 'compact'] as const, 'comfortable'),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* preference is cosmetic; storage failures are not worth surfacing */
    }
  }, [theme]);

  useEffect(() => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch {
      /* as above */
    }
  }, [density]);

  const setTheme = useCallback((next: Theme) => setThemeState(next), []);
  const toggleTheme = useCallback(
    () => setThemeState((current) => (current === 'dark' ? 'light' : 'dark')),
    [],
  );
  const setDensity = useCallback((next: TableDensity) => setDensityState(next), []);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, density, setTheme, toggleTheme, setDensity }),
    [theme, density, setTheme, toggleTheme, setDensity],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
