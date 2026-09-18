// @vitest-environment jsdom
/**
 * The screen the link in the new mailbox opens.
 *
 * It is signed out and it acts on arrival, so the two things worth holding are
 * that it posts the token exactly once — the token is single use, and a second
 * post would report the first one's success as a failure — and that a dead or
 * missing link says so plainly instead of leaving a spinner running.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VerifyEmailPage } from './VerifyEmailPage';

const MOVED = {
  id: 1,
  email: 'priya@deodap.in',
  full_name: 'Priya Mehta',
  role: 'Inventory lead',
  timezone: 'Asia/Kolkata',
  initials: 'P',
  workspace: {
    id: 1,
    name: 'Deodap Retail',
    slug: 'deodap',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    low_stock_threshold: 10,
  },
  preferences: { theme: 'light', table_density: 'comfortable', alert_on_stockout: true },
};

type Call = { path: string; body: unknown };

function stub(reply: { ok: boolean; status: number; body: unknown }) {
  const calls: Call[] = [];
  // Parameters declared, or `mock.calls` is typed as an empty tuple.
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      path: String(input).replace(/^.*\/api/, '').split('?')[0] ?? '',
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
    });
    return Promise.resolve({
      ok: reply.ok,
      status: reply.status,
      json: () => Promise.resolve(reply.body),
    } as Response);
  });
  vi.stubGlobal('fetch', mock);
  return calls;
}

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/verify-email${search}`]}>
      <Routes>
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/login" element={<div>Sign in</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Verify email', () => {
  it('sends the token once and confirms the new address', async () => {
    const calls = stub({ ok: true, status: 200, body: MOVED });
    renderAt('?token=abc.def.ghi');

    expect(await screen.findByText('Email updated')).toBeDefined();
    expect(screen.getByText('priya@deodap.in')).toBeDefined();

    const posts = calls.filter((call) => call.path === '/auth/verify-email');
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({ token: 'abc.def.ghi' });
  });

  it('says the password is untouched, because only the address moved', async () => {
    stub({ ok: true, status: 200, body: MOVED });
    renderAt('?token=abc.def.ghi');

    await screen.findByText('Email updated');
    expect(document.body.textContent).toContain('Your password has not changed.');
  });

  it('shows the server refusal for a spent or expired link', async () => {
    stub({
      ok: false,
      status: 400,
      body: {
        error: {
          code: 'email_token_invalid',
          message: 'This verification link is no longer valid.',
          next: 'Request the change again from Settings.',
        },
      },
    });
    renderAt('?token=spent');

    expect(await screen.findByText('This verification link is no longer valid.')).toBeDefined();
    expect(screen.getByText('Request the change again from Settings.')).toBeDefined();
  });

  it('does not call the server when the link arrived without a token', async () => {
    const calls = stub({ ok: true, status: 200, body: MOVED });
    renderAt('');

    expect(await screen.findByText('This link is incomplete.')).toBeDefined();
    await waitFor(() =>
      expect(calls.filter((call) => call.path === '/auth/verify-email')).toHaveLength(0),
    );
  });
});
