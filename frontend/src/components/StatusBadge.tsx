import { Icon } from './Icon';
import type { ImportStatus } from '../types/api';

/**
 * The three import badges from design doc §8.8.
 *
 * Tone follows the product's status palette (§1.3): Moss for landed, Amber for
 * needs-review, Rust for failed. 'partial' is amber rather than rust because
 * rows did arrive — it is a review prompt, not a failure.
 */
const TONES: Record<ImportStatus, { tone: string; label: string }> = {
  pending: { tone: '', label: 'Pending' },
  reading: { tone: 'slate', label: 'Reading' },
  validating: { tone: 'slate', label: 'Validating' },
  saving: { tone: 'slate', label: 'Saving' },
  complete: { tone: 'moss', label: 'Complete' },
  partial: { tone: 'amber', label: 'Partial' },
  failed: { tone: 'rust', label: 'Failed' },
};

export function StatusBadge({ status }: { status: ImportStatus }) {
  const { tone, label } = TONES[status] ?? TONES.pending;
  return (
    <span className={`badge ${tone}`}>
      <span className={`dot ${tone}`} />
      {label}
    </span>
  );
}

/** Design doc §9.1: the result column in Sync history. */
const SYNC_TONES: Record<string, { tone: string; label: string }> = {
  success: { tone: 'moss', label: 'Success' },
  // Amber, not rust: rows landed. It is a review prompt, not a failure.
  partial: { tone: 'amber', label: 'Partial' },
  failed: { tone: 'rust', label: 'Failed' },
};

export function SyncResultBadge({
  result,
  running,
}: {
  result: string | null;
  running: boolean;
}) {
  if (running) {
    return (
      <span className="badge">
        <span className="dot slate" />
        Running
      </span>
    );
  }
  const known = result ? SYNC_TONES[result] : undefined;
  if (!known) return <span className="badge">—</span>;
  return (
    <span className={`badge ${known.tone}`}>
      <span className={`dot ${known.tone}`} />
      {known.label}
    </span>
  );
}

export function ConnectionBadge({ status }: { status: string }) {
  if (status === 'connected') {
    return (
      <span className="badge moss">
        <span className="dot moss" />
        Live
      </span>
    );
  }
  if (status === 'token_expired') {
    return (
      <span className="badge rust">
        <Icon name="warn" size="s" />
        Token expired
      </span>
    );
  }
  // Separate from "Token expired" on purpose: regenerating the token does not
  // fix a scope that was never granted, and sending someone down that path
  // costs them a round trip through the Shopify admin for nothing.
  if (status === 'missing_scopes') {
    return (
      <span className="badge amber">
        <Icon name="warn" size="s" />
        Missing scopes
      </span>
    );
  }
  return <span className="badge">Disconnected</span>;
}
