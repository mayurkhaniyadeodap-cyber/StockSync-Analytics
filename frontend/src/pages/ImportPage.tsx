import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../components/Icon';
import type { IconName } from '../components/Icon';
import { StatusBadge } from '../components/StatusBadge';
import { SyncAfterImport } from '../components/SyncAfterImport';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { useToast } from '../hooks/useToast';
import { StockSyncApiError, api } from '../lib/api';
import { n, pct } from '../lib/format';
import { IMPORT_WARNINGS } from '../lib/labels';
import type { ImportResult, InventorySummary } from '../types/api';

const ACCEPTED = ['.csv', '.xlsx'];

type Method = 'csv' | 'excel' | 'gsheet';

/** How the chosen method collects its input. */
type Kind = 'file' | 'sheet';

interface MethodMeta {
  key: Method;
  icon: IconName;
  title: string;
  blurb: string;
  /** Panel heading once chosen. */
  heading: string;
  hint: string;
  /** Full-width row at the foot of the grid. */
  wide?: boolean;
  kind: Kind;
}

/**
 * The three import methods, in the prototype's order.
 *
 * CSV and Excel post a file to `/imports/upload`; Google Sheet posts a link to
 * `/imports/google-sheet`, where the server exports the sheet as CSV and runs
 * the same import. All three return the same `ImportResult`, which is why the
 * summary, the toast and the error handling below are shared rather than
 * duplicated per method.
 */
const METHODS: MethodMeta[] = [
  {
    key: 'csv',
    icon: 'file',
    title: 'CSV file',
    blurb: 'One-time upload, up to 25 MB',
    heading: 'Upload a CSV file',
    hint: 'Only a SKU column is required. Column names are matched automatically.',
    kind: 'file',
  },
  {
    key: 'excel',
    icon: 'file',
    title: 'Excel file',
    blurb: '.xlsx or .xls, one-time upload',
    heading: 'Upload an Excel file',
    hint: 'Only a SKU column is required. Column names are matched automatically.',
    kind: 'file',
  },
  {
    key: 'gsheet',
    icon: 'sheet',
    title: 'Google Sheet',
    blurb:
      'Paste the link and StockSync Analytics reads the sheet directly — nothing to download or upload first.',
    heading: 'Connect a Google Sheet',
    hint: 'Only a SKU column is required. Column names are matched automatically.',
    wide: true,
    kind: 'sheet',
  },
];

/**
 * Design doc §8.1–§8.7.
 *
 * The page opens on the method grid rather than on a drop zone: three sources
 * that all end at the same import, and choosing one is the first decision.
 * The file drop zone lives inside the CSV and Excel panels, where the prototype
 * puts it.
 */
export function ImportPage() {
  const navigate = useNavigate();
  const { toast } = useToast();

  const inputRef = useRef<HTMLInputElement>(null);
  const [method, setMethod] = useState<Method | null>(null);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [sheetLink, setSheetLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<{ message: string; next: string } | null>(null);
  const [summary, setSummary] = useState<InventorySummary | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await api.get<InventorySummary>('/inventory/summary'));
    } catch {
      // The header figure is context, not the task. A failure here must not
      // block the upload the user came to do.
      setSummary(null);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  function choose(next: File | null) {
    setFile(next);
    setResult(null);
    setError(null);
  }

  function accepted(name: string) {
    return ACCEPTED.some((ext) => name.toLowerCase().endsWith(ext));
  }

  function reset() {
    setMethod(null);
    setSheetLink('');
    choose(null);
    if (inputRef.current) inputRef.current.value = '';
  }

  /**
   * Both sources end here.
   *
   * The two endpoints return the same `ImportResult` and the same error
   * envelope, so everything after the request — the summary, the toast, the
   * refreshed header figure, the message on failure — is one code path. That is
   * what makes "the same messages as a normal CSV upload" true by construction
   * rather than by two sets of copy that have to be kept in step.
   */
  async function run(send: () => Promise<ImportResult>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      const outcome = await send();
      setResult(outcome);
      setFile(null);
      setSheetLink('');
      if (inputRef.current) inputRef.current.value = '';
      toast(
        `${n(outcome.batch.rows_imported)} SKUs imported`,
        outcome.batch.status === 'partial' ? 'amber' : 'moss',
      );
      void loadSummary();
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError
          ? { message: caught.message, next: caught.next }
          : { message: fallback, next: 'Try again.' },
      );
    } finally {
      setBusy(false);
    }
  }

  /** Unchanged: one multipart POST to the one import endpoint. */
  function upload() {
    if (!file) return;
    void run(
      () => api.upload<ImportResult>('/imports/upload', file),
      'That file could not be imported.',
    );
  }

  /** §8.4 — the server exports the sheet as CSV, then runs the same import. */
  function connectSheet() {
    const trimmed = sheetLink.trim();
    if (!trimmed) return;
    void run(
      () => api.post<ImportResult>('/imports/google-sheet', { url: trimmed }),
      'That sheet could not be imported.',
    );
  }

  const chosen = METHODS.find((entry) => entry.key === method) ?? null;

  return (
    <Page>
      <PageHeader
        title="Inventory import"
        subtitle={
          summary && summary.total_skus > 0
            ? `${n(summary.total_skus)} SKUs on file · ${n(summary.total_quantity)} units`
            : 'Bring in a stock sheet'
        }
        actions={
          <button className="btn" onClick={() => navigate('/import-history')}>
            <Icon name="clock" size="s" /> Import history
          </button>
        }
      />

      {result ? (
        <ImportSummary
          result={result}
          onAnother={() => {
            setResult(null);
            reset();
          }}
        />
      ) : (
        <>
          <div className="panel">
            <div className="p-hd">
              <h3>Choose a method</h3>
              <span className="hint">All three end at the same import</span>
            </div>
            <div className="p-bd">
              <div className="methods">
                {METHODS.map((entry) => (
                  <button
                    key={entry.key}
                    className={[
                      'method',
                      entry.wide ? 'wide' : '',
                      method === entry.key ? 'on' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    aria-pressed={method === entry.key}
                    onClick={() => {
                      setMethod(entry.key);
                      choose(null);
                    }}
                  >
                    <div className="mi">
                      <Icon name={entry.icon} />
                    </div>
                    {entry.wide ? (
                      <>
                        <div className="mtext">
                          <h4>{entry.title}</h4>
                          <p>{entry.blurb}</p>
                        </div>
                        <Icon name="right" style={{ color: 'var(--ink-45)' }} />
                      </>
                    ) : (
                      <>
                        <h4>{entry.title}</h4>
                        <p>{entry.blurb}</p>
                      </>
                    )}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {chosen && (
            <div className="panel">
              <div className="p-hd">
                <h3>{chosen.heading}</h3>
                <div className="r">
                  <button className="btn sm" onClick={reset}>
                    Change method
                  </button>
                </div>
              </div>

              <div className="p-bd">
                {error && (
                  <div className="banner err" role="alert">
                    <Icon name="warn" size="s" style={{ marginTop: 2 }} />
                    <span>
                      <b>{error.message}</b> {error.next}
                    </span>
                  </div>
                )}

                {chosen.kind === 'sheet' ? (
                  <div className="field">
                    <label htmlFor="sheet-link">
                      Google Sheet link <span className="req">*</span>
                    </label>
                    <input
                      id="sheet-link"
                      className="inp"
                      type="url"
                      inputMode="url"
                      value={sheetLink}
                      onChange={(e) => {
                        setSheetLink(e.target.value);
                        setError(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') connectSheet();
                      }}
                      placeholder="https://docs.google.com/spreadsheets/…"
                      autoComplete="off"
                      spellCheck={false}
                      disabled={busy}
                    />
                    <div className="help">
                      You&rsquo;ll be asked to grant read-only access to this sheet.
                    </div>
                  </div>
                ) : file ? (
                  <div className="filechip">
                    <span className="fi">
                      <Icon name="check" size="s" />
                    </span>
                    <div style={{ flex: 1 }}>
                      <b>{file.name}</b>
                      <div className="help" style={{ marginTop: 2 }}>
                        {(file.size / 1024).toFixed(0)} KB
                      </div>
                    </div>
                    <button className="btn sm" onClick={() => choose(null)} disabled={busy}>
                      Remove
                    </button>
                  </div>
                ) : (
                  <div
                    className={`drop${dragging ? ' over' : ''}`}
                    onDragOver={(e) => {
                      e.preventDefault();
                      setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDragging(false);
                      const dropped = e.dataTransfer.files[0];
                      if (!dropped) return;
                      if (!accepted(dropped.name)) {
                        setError({
                          message: 'That file type isn’t supported.',
                          next: 'Upload a .csv or .xlsx file.',
                        });
                        return;
                      }
                      choose(dropped);
                    }}
                  >
                    <h4>Drop your file here, or browse</h4>
                    <p>Supports {chosen.key === 'csv' ? '.csv' : '.xlsx'} up to 25 MB</p>
                    <div style={{ marginTop: 14 }}>
                      <button className="btn" onClick={() => inputRef.current?.click()}>
                        Browse files
                      </button>
                    </div>
                    <input
                      ref={inputRef}
                      type="file"
                      accept=".csv,.xlsx"
                      hidden
                      onChange={(e) => choose(e.target.files?.[0] ?? null)}
                    />
                  </div>
                )}

                {chosen.kind === 'file' && (
                  <div className="notice" style={{ marginTop: 14 }}>
                    <Icon name="warn" size="s" style={{ marginTop: 1 }} />
                    <div>
                      A one-time upload can&rsquo;t be re-run later. Connect a Google Sheet if
                      this file changes often.
                    </div>
                  </div>
                )}
              </div>
              <div className="p-ft">
                <span className="hint">{chosen.hint}</span>
                <span className="spacer" />
                <button
                  className="btn cta"
                  onClick={chosen.kind === 'sheet' ? connectSheet : upload}
                  disabled={busy || (chosen.kind === 'sheet' ? !sheetLink.trim() : !file)}
                >
                  {busy ? (
                    <>
                      <span className="dot slate" style={{ background: 'currentColor' }} />{' '}
                      Importing…
                    </>
                  ) : chosen.kind === 'sheet' ? (
                    'Connect'
                  ) : (
                    'Import file'
                  )}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Page>
  );
}

/** Design doc §8.7 — what landed, what did not, and why. */
function ImportSummary({ result, onAnother }: { result: ImportResult; onAnother: () => void }) {
  const navigate = useNavigate();
  const { batch, analysis } = result;

  return (
    <>
      {/* Above the figures, because it is the one thing on this screen still
          in motion — and because a failed sync has to be read before the
          numbers it did not update. */}
      <SyncAfterImport sync={result.sync} />

      <div className="panel">
        <div className="p-hd">
          <h3>{batch.origin_filename}</h3>
          <div className="r">
            <StatusBadge status={batch.status} />
          </div>
        </div>
        <div className="stat-row">
          <div className="stat-cell">
            <div className="stat-lbl">Rows read</div>
            <div className="stat-val num">{n(batch.rows_read)}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-lbl">SKUs imported</div>
            <div className="stat-val num">{n(batch.rows_imported)}</div>
            <div className="stat-note">
              {n(result.items_created)} new · {n(result.items_updated)} updated
              {/* An import replaces the dataset, so this is a real removal
                  rather than a filter. Said here because the number it changes
                  — every figure on the Dashboard — is on the next screen. */}
              {result.items_removed > 0 ? <> · {n(result.items_removed)} removed</> : null}
            </div>
          </div>
          <div className="stat-cell">
            <div className="stat-lbl">Duplicates merged</div>
            <div className="stat-val num">{n(batch.rows_merged)}</div>
            <div className="stat-note">Quantities summed</div>
          </div>
          <div className="stat-cell">
            <div className="stat-lbl">Rows rejected</div>
            <div className="stat-val num">{n(batch.rows_rejected)}</div>
          </div>
        </div>
        {/* The import is not finished when the rows land — it is finished when
            those SKUs have been matched against Shopify sales. Saying so here
            is what removes the trip to the dashboard to find out, and the
            manual Recompute that used to come first. */}
        <div className="p-bd" style={{ borderTop: '1px solid var(--line)' }}>
          <div className="kv">
            <div>
              <div className="k" style={{ color: 'var(--ink)', fontWeight: 600 }}>
                Analysis complete
              </div>
              <div className="k" style={{ fontSize: 12 }}>
                {n(analysis.skus_matched)} of {n(analysis.skus_analyzed)} SKUs matched a Shopify
                sale · {n(analysis.shopify_sales)} units ({pct(analysis.shopify_sales_pct)} of
                the store) · {n(analysis.total_complaints)} complaints
              </div>
            </div>
            <button className="btn sm" onClick={() => navigate('/dashboard')}>
              View dashboard <Icon name="right" size="s" />
            </button>
          </div>
        </div>

        <div className="p-ft">
          <span className="hint">
            {result.sheet_format === 'complaints'
              ? `Read as complaint rows and grouped by SKU · header row ${String(
                  result.header_row_number,
                )}`
              : `Header row ${String(result.header_row_number)}`}{' '}
            · matched{' '}
            {Object.entries(result.detected_columns)
              .map(([field, column]) => `${column} → ${field}`)
              .join(', ')}
          </span>
          <span className="spacer" />
          <button className="btn" onClick={onAnother}>
            Import another file
          </button>
          <button className="btn pri" onClick={() => navigate('/import-history')}>
            View import history
          </button>
        </div>
      </div>

      {(result.warnings ?? []).length > 0 && (
        <div className="panel">
          <div className="p-hd">
            <h3>Worth checking</h3>
            <span className="hint">The file imported, but not the way you may have meant</span>
          </div>
          <div className="p-bd">
            {(result.warnings ?? []).map((code) => {
              const warning = IMPORT_WARNINGS[code];
              return (
                <div className="inline-err" key={code} role="status">
                  <Icon name="warn" />
                  <div>
                    <b>{warning?.title ?? 'Something in this file was not used.'}</b>{' '}
                    {warning?.body ??
                      'Check the file against the expected format and import it again.'}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {Object.keys(result.unmapped_reasons).length > 0 && (
        <div className="panel">
          <div className="p-hd">
            <h3>Reasons not recognised</h3>
            <span className="hint">
              These rows counted towards Total Count, Orders and Qty — only the complaint
              breakdown missed them
            </span>
          </div>
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Reason</th>
                  <th className="n">Rows</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.unmapped_reasons).map(([reason, rows]) => (
                  <tr key={reason}>
                    <td>{reason}</td>
                    <td className="n">{n(rows)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {result.duplicates.length > 0 && (
        <div className="panel">
          <div className="p-hd">
            <h3>
              {result.sheet_format === 'complaints' ? 'SKUs grouped' : 'Duplicate SKUs merged'}
            </h3>
            <span className="hint">
              {result.sheet_format === 'complaints'
                ? 'Several complaint rows became one SKU; their counts were added together'
                : 'The same SKU appeared more than once; quantities were added together'}
            </span>
          </div>
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>Rows</th>
                  <th className="n">Merged quantity</th>
                </tr>
              </thead>
              <tbody>
                {result.duplicates.map((group) => (
                  <tr key={group.sku}>
                    <td>
                      <span className="sku">{group.sku}</span>
                    </td>
                    <td>{group.rows.join(', ')}</td>
                    <td className="n">{n(group.merged_quantity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.duplicates_truncated && (
            <div className="tbl-ft">
              <span>Showing the first 50 duplicate groups.</span>
            </div>
          )}
        </div>
      )}

      {result.rejected.length > 0 && (
        <div className="panel">
          <div className="p-hd">
            <h3>Rows not imported</h3>
            <span className="hint">Everything else in the file was imported</span>
          </div>
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th className="n">Row</th>
                  <th>Reason</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {result.rejected.map((row) => (
                  <tr key={row.row_number}>
                    <td className="n">{row.row_number}</td>
                    <td>{row.reason.replace(/_/g, ' ')}</td>
                    <td>{row.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.rejected_truncated && (
            <div className="tbl-ft">
              <span>Showing the first 50 rejected rows.</span>
            </div>
          )}
        </div>
      )}
    </>
  );
}
