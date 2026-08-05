import { createContext, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { Icon } from '../components/Icon';

export type ToastTone = 'slate' | 'moss' | 'amber' | 'rust';

export interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
  /** Failures stay until dismissed — design doc §5.8. */
  persist: boolean;
  leaving?: boolean;
}

export interface ToastContextValue {
  toasts: Toast[];
  toast: (message: string, tone?: ToastTone, persist?: boolean) => number;
  dismiss: (id: number) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 5000;
const LEAVE_ANIMATION_MS = 180;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const remove = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
    // Both of this toast's timers: the auto-dismiss under `id` and the leave
    // animation under `-id`. Dismissing by hand cancels the one still pending.
    for (const key of [id, -id]) {
      const timer = timers.current.get(key);
      if (timer) {
        clearTimeout(timer);
        timers.current.delete(key);
      }
    }
  }, []);

  const dismiss = useCallback(
    (id: number) => {
      // Mark first so the exit animation can play, then remove.
      setToasts((current) => current.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
      // Tracked like the auto-dismiss one: an untracked timer outlives the
      // provider and fires against a component that is no longer mounted.
      // Keyed negatively so it cannot collide with that toast's own entry.
      timers.current.set(
        -id,
        setTimeout(() => remove(id), LEAVE_ANIMATION_MS),
      );
    },
    [remove],
  );

  // Nothing should still be scheduled once the provider is gone — on a route
  // change or a sign-out, a pending dismissal has nothing left to dismiss.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);

  const toast = useCallback(
    (message: string, tone: ToastTone = 'slate', persist = false) => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, tone, persist }]);

      if (!persist) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), AUTO_DISMISS_MS),
        );
      }
      return id;
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(
    () => ({ toasts, toast, dismiss }),
    [toasts, toast, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/** Bottom-centre, per design doc §5.8. */
function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast${t.leaving ? ' out' : ''}`}>
          <span className="tc" style={{ background: `var(--${t.tone})` }} />
          <span>{t.message}</span>
          <button
            className="icon-btn tx"
            style={{ width: 20, height: 20 }}
            onClick={() => onDismiss(t.id)}
            aria-label="Dismiss notification"
          >
            <Icon name="x" size="s" />
          </button>
        </div>
      ))}
    </div>
  );
}
