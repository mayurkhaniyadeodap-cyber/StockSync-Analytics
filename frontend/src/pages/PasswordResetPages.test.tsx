// @vitest-environment jsdom
/**
 * The two signed-out reset screens.
 *
 * The assertion that matters most is what the success screen does *not* say:
 * the server answers a reset request identically whether or not the address
 * exists, and it would be easy to undo that on the client with a friendly
 * "Check your inbox — we found your account!".
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ForgotPasswordPage } from './ForgotPasswordPage';
import { ResetPasswordPage } from './ResetPasswordPage';
import { AuthProvider } from '../contexts/AuthContext';

/** The fixed sentence the API returns, whatever the address was. */
const ACCEPTED =
  'If that address has an account, a reset link is on its way. ' +
  'The link expires shortly and can be used once.';

function stub(reply: { ok: boolean; status: number; body: unknown }) {
  // Parameters declared, or `mock.calls` is typed as an empty tuple and every
  // `calls[0]` below is a type error.
  const mock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve({
      ok: reply.ok,
      status: reply.status,
      json: () => Promise.resolve(reply.body),
    } as Response),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
}

function renderAt(path: string, element: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/forgot-password" element={element} />
          <Route path="/reset-password" element={element} />
          <Route path="/dashboard" element={<div>Dashboard</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Forgot password', () => {
  it('asks for the address and nothing else', () => {
    stub({ ok: true, status: 200, body: {} });
    renderAt('/forgot-password', <ForgotPasswordPage />);

    expect(screen.getByLabelText('Email address')).toBeDefined();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
  });

  it('refuses a malformed address before calling the server', async () => {
    const fetchMock = stub({ ok: true, status: 200, body: {} });
    renderAt('/forgot-password', <ForgotPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Email address'), 'not-an-address');
    await user.click(screen.getByRole('button', { name: 'Send reset link' }));

    expect(await screen.findByText('Enter a valid email address.')).toBeDefined();
    // Scoped to the endpoint: AuthProvider calls /auth/me on mount, so the spy
    // is never idle.
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes('/auth/forgot-password')),
    ).toBe(false);
  });

  it('posts the address to the reset endpoint', async () => {
    const fetchMock = stub({ ok: true, status: 200, body: { detail: ACCEPTED } });
    renderAt('/forgot-password', <ForgotPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Email address'), 'admin@deodap.in');
    await user.click(screen.getByRole('button', { name: 'Send reset link' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) =>
        String(input).includes('/auth/forgot-password'),
      );
      expect(call).toBeDefined();
      expect(String(call?.[1]?.body)).toContain('admin@deodap.in');
    });
  });

  it('shows the server’s own sentence, and claims nothing beyond it', async () => {
    /**
     * The server is deliberately non-committal about whether the address had an
     * account. Writing "we found you" here would undo that on the client and
     * turn the page back into an enumeration oracle.
     */
    stub({ ok: true, status: 200, body: { detail: ACCEPTED } });
    renderAt('/forgot-password', <ForgotPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Email address'), 'admin@deodap.in');
    await user.click(screen.getByRole('button', { name: 'Send reset link' }));

    expect(await screen.findByText(ACCEPTED)).toBeDefined();
    expect(screen.queryByText(/we found/i)).toBeNull();
    expect(screen.queryByText(/that account/i)).toBeNull();
  });

  it('offers the way back to sign in', () => {
    stub({ ok: true, status: 200, body: {} });
    renderAt('/forgot-password', <ForgotPasswordPage />);

    expect(screen.getByRole('link', { name: 'Back to sign in' })).toBeDefined();
  });
});

describe('Reset password', () => {
  it('says so when the link carries no token', () => {
    /** The form would only fail on submit; saying it now saves the round trip
        and the typing. */
    stub({ ok: true, status: 200, body: {} });
    renderAt('/reset-password', <ResetPasswordPage />);

    expect(screen.getByText('This link is incomplete')).toBeDefined();
    expect(screen.getByRole('link', { name: 'Request a new link' })).toBeDefined();
  });

  it('never renders the token', () => {
    /** A token on screen is a token in a screenshot. */
    stub({ ok: true, status: 200, body: {} });
    const { container } = renderAt(
      '/reset-password?token=secret-token-value',
      <ResetPasswordPage />,
    );

    expect(container.textContent).not.toContain('secret-token-value');
    expect(container.innerHTML).not.toContain('secret-token-value');
  });

  it('refuses a mismatch without calling the server', async () => {
    /** A mismatch is a typing mistake, not a security decision — there is
        nothing for the server to rule on. */
    const fetchMock = stub({ ok: true, status: 200, body: {} });
    renderAt('/reset-password?token=abc', <ResetPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('New password'), 'a-long-enough-password');
    await user.type(screen.getByLabelText('Confirm new password'), 'something-else-entirely');
    await user.click(screen.getByRole('button', { name: 'Set password and sign in' }));

    expect(await screen.findByText('The two passwords do not match.')).toBeDefined();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes('/auth/reset-password')),
    ).toBe(false);
  });

  it('sends the token and the password when they match', async () => {
    const fetchMock = stub({ ok: true, status: 200, body: { id: 1, email: 'a@b.co' } });
    renderAt('/reset-password?token=abc', <ResetPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('New password'), 'a-long-enough-password');
    await user.type(screen.getByLabelText('Confirm new password'), 'a-long-enough-password');
    await user.click(screen.getByRole('button', { name: 'Set password and sign in' }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) =>
        String(input).includes('/auth/reset-password'),
      );
      expect(call).toBeDefined();
      const body = String(call?.[1]?.body);
      expect(body).toContain('abc');
      expect(body).toContain('a-long-enough-password');
    });
  });

  it('shows the server’s refusal rather than inventing one', async () => {
    /** The length floor is the server's rule. Duplicating it here is how the
        two drift apart. */
    stub({
      ok: false,
      status: 422,
      body: {
        error: {
          code: 'password_too_short',
          message: 'Choose a password of at least 12 characters.',
          next: 'A longer passphrase is easier to remember and harder to guess.',
        },
      },
    });
    renderAt('/reset-password?token=abc', <ResetPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('New password'), 'short');
    await user.type(screen.getByLabelText('Confirm new password'), 'short');
    await user.click(screen.getByRole('button', { name: 'Set password and sign in' }));

    expect(
      await screen.findByText('Choose a password of at least 12 characters.'),
    ).toBeDefined();
  });

  it('reports an expired link with the way to get a new one', async () => {
    stub({
      ok: false,
      status: 400,
      body: {
        error: {
          code: 'reset_token_invalid',
          message: 'This reset link is no longer valid.',
          next: 'Request a new link from the sign-in page.',
        },
      },
    });
    renderAt('/reset-password?token=stale', <ResetPasswordPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('New password'), 'a-long-enough-password');
    await user.type(screen.getByLabelText('Confirm new password'), 'a-long-enough-password');
    await user.click(screen.getByRole('button', { name: 'Set password and sign in' }));

    expect(await screen.findByText('This reset link is no longer valid.')).toBeDefined();
    expect(screen.getByText(/Request a new link from the sign-in page/)).toBeDefined();
  });
});
