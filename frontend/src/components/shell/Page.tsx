import type { ReactNode } from 'react';

/** Centres and pads page content to the shared max width. */
export function Page({ children }: { children: ReactNode }) {
  return (
    <section className="page on">
      <div className="wrap">{children}</div>
    </section>
  );
}
