import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  /** One-line purpose, or the freshness label ("Synced 12 minutes ago"). */
  subtitle?: ReactNode;
  /** Secondary then primary, right-aligned. One primary CTA per screen. */
  actions?: ReactNode;
}

/**
 * The page template every interior page uses (design doc §2.2).
 *
 * Keeping it in one component is what makes the promise in the doc true — that
 * the title, actions and filters sit in the same place in every module, so
 * users build muscle memory.
 */
export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <div className="sub">{subtitle}</div>}
      </div>
      {actions && <div className="acts">{actions}</div>}
    </div>
  );
}
