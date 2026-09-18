/**
 * The header every panel in the application shares.
 *
 * `.p-hd` markup was written out by hand at forty-odd call sites, each deciding
 * for itself whether the subtitle came before or after the buttons and whether
 * there was a glyph. This is that markup, once, so a panel on Reports and a
 * panel on Sales Analytics are the same object.
 *
 * The glyph is optional but the tone is not arbitrary: it is the same status
 * palette as badges and dots, so a rust chip means on this panel what a rust
 * badge means in the table below it.
 */

import { useId, useState } from 'react';
import type { ReactNode } from 'react';

import { Icon } from '../Icon';
import type { IconName } from '../Icon';

export type ChipTone = 'slate' | 'moss' | 'amber' | 'rust' | 'clay';

export interface PanelHeadProps {
  title: string;
  /** One line under the title saying what the panel counts. */
  hint?: ReactNode;
  icon?: IconName;
  tone?: ChipTone;
  /** Buttons and controls, right-aligned on the title's own line. */
  actions?: ReactNode;
  /**
   * A caveat about what the figures do and do not cover, behind a disclosure.
   *
   * These used to sit open under every chart. They are worth keeping — each one
   * stops a real misreading — but three of them along a row of three panels
   * made the panels three different heights and buried the charts under prose.
   * Folded away, the caveat is one click from the figure it qualifies and costs
   * the layout nothing until someone asks for it.
   */
  info?: ReactNode;
}

export function PanelHead({
  title,
  hint,
  icon,
  tone = 'slate',
  actions,
  info,
}: PanelHeadProps) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <div className="p-hd">
      {icon ? (
        <span className={`p-chip ${tone}`} aria-hidden="true">
          <Icon name={icon} size="s" />
        </span>
      ) : null}
      <h3>{title}</h3>

      {info ? (
        <button
          className={`p-info${open ? ' on' : ''}`}
          onClick={() => setOpen((shown) => !shown)}
          aria-expanded={open}
          aria-controls={id}
          aria-label={open ? `Hide what ${title} covers` : `What does ${title} cover?`}
        >
          <Icon name="warn" size="s" />
        </button>
      ) : null}

      {actions ? <div className="r">{actions}</div> : null}
      {hint ? <span className="hint">{hint}</span> : null}

      {/* Always rendered, so the control has something to point `aria-controls`
          at whether or not it is open. */}
      {info ? (
        <div className="p-note" id={id} hidden={!open}>
          {info}
        </div>
      ) : null}
    </div>
  );
}
