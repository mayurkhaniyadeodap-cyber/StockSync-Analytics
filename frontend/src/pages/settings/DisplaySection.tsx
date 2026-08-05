/**
 * Settings → Display.
 *
 * Three switches that are yours alone, and one number that is not: the low
 * stock threshold sets the line everyone's Low stock figure is drawn at, so the
 * panel says so rather than letting it look like a personal preference.
 */

import { useEffect, useState } from 'react';

import { useAuth } from '../../hooks/useAuth';
import { useTheme } from '../../hooks/useTheme';
import { useToast } from '../../hooks/useToast';

export function DisplaySection() {
  const { theme, density, toggleTheme, setDensity } = useTheme();
  const { user, savePreferences } = useAuth();
  const { toast } = useToast();

  const saved = user?.workspace.low_stock_threshold ?? 0;
  const [threshold, setThreshold] = useState(String(saved));
  const [savingThreshold, setSavingThreshold] = useState(false);

  useEffect(() => {
    setThreshold(String(saved));
  }, [saved]);

  /** Design doc §13: saving shows an inline confirmation, not a page-level toast. */
  async function persist(patch: Parameters<typeof savePreferences>[0]) {
    try {
      await savePreferences(patch);
    } catch {
      toast("Couldn't save that setting. Check your connection and try again.", 'rust', true);
    }
  }

  async function saveThreshold() {
    const parsed = Number(threshold);
    // A blank or nonsense box reverts rather than saving something arbitrary:
    // this number changes a figure everyone sees.
    if (!Number.isInteger(parsed) || parsed < 0) {
      setThreshold(String(saved));
      return;
    }
    if (parsed === saved) return;

    setSavingThreshold(true);
    await persist({ low_stock_threshold: parsed });
    setSavingThreshold(false);
  }

  return (
    <div className="panel">
      <div className="p-hd">
        <h3>Display</h3>
      </div>
      <div className="p-bd">
        <div className="kv">
          <div>
            <div className="k" style={{ color: 'var(--ink)', fontWeight: 600 }}>
              Dark theme
            </div>
            <div className="k" style={{ fontSize: 12 }}>
              Same palette, inverted for low-light warehouses.
            </div>
          </div>
          <button
            className={`switch${theme === 'dark' ? ' on' : ''}`}
            role="switch"
            aria-checked={theme === 'dark'}
            aria-label="Dark theme"
            onClick={() => {
              const next = theme === 'dark' ? 'light' : 'dark';
              toggleTheme();
              void persist({ theme: next });
            }}
          />
        </div>

        <div className="kv">
          <div>
            <div className="k" style={{ color: 'var(--ink)', fontWeight: 600 }}>
              Compact tables by default
            </div>
            <div className="k" style={{ fontSize: 12 }}>
              Applies to every table in StockSync Analytics.
            </div>
          </div>
          <button
            className={`switch${density === 'compact' ? ' on' : ''}`}
            role="switch"
            aria-checked={density === 'compact'}
            aria-label="Compact tables by default"
            onClick={() => {
              const next = density === 'compact' ? 'comfortable' : 'compact';
              setDensity(next);
              void persist({ table_density: next });
            }}
          />
        </div>

        <div className="kv">
          <div>
            <div className="k" style={{ color: 'var(--ink)', fontWeight: 600 }}>
              Alert me when stock runs out
            </div>
            <div className="k" style={{ fontSize: 12 }}>
              One notification per sync, not per SKU.
            </div>
          </div>
          <button
            className={`switch${user?.preferences.alert_on_stockout ? ' on' : ''}`}
            role="switch"
            aria-checked={user?.preferences.alert_on_stockout ?? false}
            aria-label="Alert me when stock runs out"
            onClick={() =>
              void persist({ alert_on_stockout: !user?.preferences.alert_on_stockout })
            }
          />
        </div>

        <div className="kv">
          <div>
            <div className="k" style={{ color: 'var(--ink)', fontWeight: 600 }}>
              Low stock threshold
            </div>
            <div className="k" style={{ fontSize: 12 }}>
              Units at or below this count are flagged low — for everyone in{' '}
              {user?.workspace.name ?? 'this workspace'}.
            </div>
          </div>
          <input
            className="inp"
            type="number"
            min={0}
            step={1}
            style={{ width: 96 }}
            value={threshold}
            aria-label="Low stock threshold"
            disabled={savingThreshold}
            onChange={(event) => setThreshold(event.target.value)}
            // On blur and on Enter, not on every keystroke: typing "20" would
            // otherwise save 2 on the way past.
            onBlur={() => void saveThreshold()}
            onKeyDown={(event) => {
              if (event.key === 'Enter') event.currentTarget.blur();
            }}
          />
        </div>
      </div>
    </div>
  );
}
