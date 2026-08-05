// @vitest-environment jsdom
/**
 * The sign-in card — design doc §6.
 *
 * The reveal toggle beside the password field is easy to get subtly wrong: a
 * button inside a form submits it unless told not to, and one that announces
 * the same label in both states tells a screen reader nothing. These hold it to
 * working the way it appears to.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LoginPage } from './LoginPage';
import { AuthProvider } from '../contexts/AuthContext';

/** Signed out: /auth/me refuses, so the provider settles on 'anonymous'. */
function signedOut() {
  return vi.fn(() =>
    Promise.resolve({
      ok: false,
      status: 401,
      json: () =>
        Promise.resolve({
          error: { code: 'not_authenticated', message: 'Sign in.', next: 'Sign in.' },
        }),
    } as Response),
  );
}

async function renderLogin() {
  vi.stubGlobal('fetch', signedOut());
  const view = render(
    <MemoryRouter>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  // The card renders only once the session check has answered.
  await screen.findByLabelText('Password');
  return view;
}

const passwordField = () => screen.getByLabelText('Password') as HTMLInputElement;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the password reveal', () => {
  it('starts hidden', async () => {
    await renderLogin();

    expect(passwordField().type).toBe('password');
    expect(screen.getByRole('button', { name: 'Show password' })).toBeDefined();
  });

  it('switches the input between password and text', async () => {
    await renderLogin();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Show password' }));
    expect(passwordField().type).toBe('text');

    await user.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(passwordField().type).toBe('password');
  });

  it('keeps what was typed when it toggles', async () => {
    /** Re-rendering the input with a new `type` must not drop its value. */
    await renderLogin();
    const user = userEvent.setup();

    await user.type(passwordField(), 'StockSync@123');
    await user.click(screen.getByRole('button', { name: 'Show password' }));

    expect(passwordField().value).toBe('StockSync@123');
  });

  it('renames itself so a screen reader hears the state change', async () => {
    await renderLogin();
    const user = userEvent.setup();

    const toggle = screen.getByRole('button', { name: 'Show password' });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(toggle.getAttribute('aria-controls')).toBe('login-password');

    await user.click(toggle);
    expect(
      screen.getByRole('button', { name: 'Hide password' }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('works from the keyboard', async () => {
    /** A real <button>, so Space and Enter act on it without a key handler. */
    await renderLogin();
    const user = userEvent.setup();

    await user.click(passwordField());
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Show password' }));

    await user.keyboard(' ');
    expect(passwordField().type).toBe('text');

    await user.keyboard('{Enter}');
    expect(passwordField().type).toBe('password');
  });

  it('does not submit the form', async () => {
    /**
     * The classic bug: a button inside a form defaults to type="submit", so
     * revealing the password would try to sign in with whatever is typed.
     */
    const fetcher = signedOut();
    vi.stubGlobal('fetch', fetcher);
    render(
      <MemoryRouter>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByLabelText('Password');
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    const before = fetcher.mock.calls.length;
    const toggle = screen.getByRole('button', { name: 'Show password' });
    expect(toggle.getAttribute('type')).toBe('button');

    await userEvent.setup().click(toggle);

    // It toggled, and nothing was posted.
    expect(passwordField().type).toBe('text');
    expect(fetcher.mock.calls.length).toBe(before);
  });
});

describe('everything else on the card', () => {
  it('still carries the fields it always had', async () => {
    await renderLogin();

    expect(screen.getByLabelText('Email')).toBeDefined();
    expect(passwordField()).toBeDefined();
    expect(screen.getByRole('checkbox', { name: /Keep me signed in/ })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Log in' })).toBeDefined();
  });

  it('keeps the password field autofillable', async () => {
    /** Toggling type must not cost the browser's password manager the field. */
    await renderLogin();

    expect(passwordField().getAttribute('autocomplete')).toBe('current-password');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Show password' }));
    expect(passwordField().getAttribute('autocomplete')).toBe('current-password');
  });
});

describe('while signing in', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const method = (init?.method ?? 'GET').toUpperCase();
        if (method === 'POST' && String(input).includes('/auth/login')) {
          // Never resolves: holds the form in its submitting state.
          return new Promise<Response>(() => {});
        }
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () =>
            Promise.resolve({
              error: { code: 'not_authenticated', message: 'Sign in.', next: 'Sign in.' },
            }),
        } as Response);
      }),
    );
  });

  it('disables the reveal toggle with the rest of the form', async () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByLabelText('Password');

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Email'), 'admin@deodap.in');
    await user.type(passwordField(), 'secret');
    await user.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Show password' })).toHaveProperty(
        'disabled',
        true,
      ),
    );
  });
});
