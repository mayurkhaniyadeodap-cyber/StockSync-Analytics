/**
 * The last line between a rendering bug and a blank page.
 *
 * React unmounts the whole tree when a render throws and nothing catches it —
 * the user is left on white, with no message, no navigation, and no way back
 * except a reload they have to think of themselves. This app had no boundary at
 * all, so one undefined field in an API payload could take down every screen.
 *
 * Two things it deliberately does **not** do:
 *
 * * **It does not reset itself.** Re-rendering the same subtree that just threw
 *   usually throws again, and a fallback that flickers is worse than one that
 *   stays. Recovery is a route change (the shell-level boundary is keyed on the
 *   path) or a reload, both of which rebuild state from the server.
 * * **It does not catch everything.** Error boundaries see render, lifecycle
 *   and constructor errors. Event handlers, `setTimeout` and rejected promises
 *   are outside React's call stack — those still need their own handling, which
 *   is what `api.ts` and the toast system already do.
 *
 * Class component because there is no hook equivalent: `getDerivedStateFromError`
 * and `componentDidCatch` have no functional counterpart in React 19.
 */

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

import { Icon } from './Icon';

interface Props {
  children: ReactNode;
  /**
   * Shown above the actions. Lets the shell say "this page" where the
   * outermost boundary has to say "the application".
   */
  scope?: string;
  /**
   * Where "Back to dashboard" should land. Omit to hide that action — the
   * boundary wrapping the login screen has nowhere useful to send anyone.
   */
  home?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // The component stack is the part a stack trace alone does not give you —
    // it names which screen died, which is the first thing anyone asks.
    console.error('[StockSync] render error', error, info.componentStack);
  }

  /**
   * A full document load, not `navigate()`.
   *
   * The React tree that threw is still the one in memory; routing inside it
   * keeps every context and cache that may be holding the bad value. Going
   * through the browser rebuilds the app from nothing, which is the only state
   * we can be sure about.
   */
  private go(to: string): void {
    window.location.assign(to);
  }

  override render(): ReactNode {
    const { error } = this.state;
    const { children, scope, home } = this.props;

    if (!error) return children;

    return (
      <div className="boot" role="alert">
        <Icon name="warn" size="l" style={{ color: 'var(--rust)' }} />

        <h1 style={{ fontSize: 20, fontWeight: 650, margin: 0 }}>Something went wrong</h1>

        <p style={{ maxWidth: 420, textAlign: 'center', color: 'var(--ink-60)' }}>
          {scope ?? 'The application'} stopped unexpectedly. Nothing you had saved is affected
          &mdash; this is a display problem, and reloading usually clears it.
        </p>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
          <button className="btn pri" onClick={() => this.go(window.location.href)}>
            <Icon name="refresh" size="s" /> Reload
          </button>
          {home ? (
            <button className="btn" onClick={() => this.go(home)}>
              Back to dashboard
            </button>
          ) : null}
        </div>

        {/* The message only, never the stack: this renders in front of the
            user, and a trace here reads as the app coming apart. The full
            error and component stack are in the console for whoever is
            actually debugging. */}
        <p
          style={{
            fontFamily: 'var(--f-mono)',
            fontSize: 11.5,
            color: 'var(--ink-45)',
            maxWidth: 420,
            textAlign: 'center',
            wordBreak: 'break-word',
          }}
        >
          {error.message}
        </p>
      </div>
    );
  }
}
