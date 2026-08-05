/**
 * The steps of one automatic workflow, as Sync History shows them.
 *
 * A run row says what it ended up as. When one comes back partial that is a
 * single error string and no account of how far it got — which stage ran, what
 * it managed, where it stopped. This is that account.
 *
 * The wording lives here rather than on the server, which sends stable
 * identifiers (`recompute_failed`) and lets the client decide how to say them.
 */

import { useEffect, useState } from 'react';

import { Icon } from './Icon';
import { Skeleton } from './Skeleton';
import { api } from '../lib/api';
import type { SyncStep } from '../types/api';

/** One line each, in the order they happen. */
const LABELS: Record<string, string> = {
  import_started: 'Import started',
  inventory_imported: 'Inventory imported',
  sync_started: 'Shopify sync started',
  sync_completed: 'Shopify sync completed',
  recompute_started: 'Analytics recompute started',
  recompute_completed: 'Analytics recompute completed',
  recompute_failed: 'Analytics recompute failed',
  workflow_finished: 'Final status',
};

const TONES: Record<string, string> = {
  ok: 'moss',
  failed: 'rust',
  started: 'slate',
};

export function SyncSteps({ runId }: { runId: number }) {
  const [steps, setSteps] = useState<SyncStep[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get<SyncStep[]>(`/shopify/syncs/${String(runId)}/steps`)
      .then((next) => {
        if (!cancelled) setSteps(next);
      })
      .catch(() => {
        // A log that will not load must not look like a run with no steps.
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (failed) {
    return <div className="stat-note">Could not load the steps for this run.</div>;
  }
  if (steps === null) {
    return <Skeleton height={14} width="60%" />;
  }
  if (steps.length === 0) {
    // Runs from before the log existed have none, which is not an error.
    return <div className="stat-note">No steps recorded for this run.</div>;
  }

  return (
    <ol className="sync-steps">
      {steps.map((step, index) => (
        <li key={`${step.step}-${String(index)}`}>
          <span className={`dot ${TONES[step.state] ?? 'slate'}`} />
          <b>{LABELS[step.step] ?? step.step}</b>
          {step.detail ? <span className="muted"> — {step.detail}</span> : null}
          <span className="at">
            {new Date(step.at).toLocaleTimeString('en-IN', {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
            })}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** The toggle that reveals the steps for one run. */
export function SyncStepsToggle({ runId }: { runId: number }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        className="btn sm"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        aria-controls={`steps-${String(runId)}`}
      >
        <Icon name={open ? 'x' : 'clock'} size="s" /> {open ? 'Hide steps' : 'Steps'}
      </button>
      {open ? (
        <div id={`steps-${String(runId)}`} style={{ marginTop: 10 }}>
          <SyncSteps runId={runId} />
        </div>
      ) : null}
    </>
  );
}
