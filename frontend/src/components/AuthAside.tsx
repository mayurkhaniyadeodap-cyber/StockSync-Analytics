/**
 * The left half of every signed-out screen.
 *
 * Shared by sign-in, forgot-password and reset-password so the three read as
 * one product rather than three forms that happen to share a colour. Below
 * 900px it is not rendered at all — a hero panel above a login form on a phone
 * is a screen of scrolling before anything useful — which is done in CSS rather
 * than with a media query hook so there is no layout shift on first paint.
 *
 * It carries the mark, a line saying what the product does, and nothing else.
 * A strip of figures sat here briefly and came out: this page renders before
 * anyone has signed in, so there is no account to read a real number from, and
 * three numbers that are really just copy read as statistics to anyone who
 * glances at them.
 */

import { Logo } from './Logo';

export function AuthAside() {
  return (
    <aside className="auth-aside" aria-hidden="true">
      <div className="auth-aside-top">
        <Logo size={36} wordmark tone="onDark" />
      </div>

      <div className="auth-aside-mid">
        <h2 className="auth-hero">
          Inventory and sales,
          <br />
          <span>reconciled.</span>
        </h2>
        <p className="auth-hero-sub">
          Your imported stock sheet matched against Shopify orders by SKU — what is selling,
          what is not, and what is about to run out.
        </p>
      </div>

      <div className="auth-aside-foot">
        © {new Date().getFullYear()} StockSync Analytics · DeoDap
      </div>
    </aside>
  );
}
