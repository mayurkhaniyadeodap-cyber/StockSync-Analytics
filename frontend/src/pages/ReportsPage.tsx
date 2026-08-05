/**
 * Reports — design doc §12.
 *
 * The flow §12.2 specifies, in order: choose a type, see exactly what the file
 * will contain, choose a format, then watch it go Preparing → Ready → Download
 * in the Export Centre. The preview and the export call the same builder on the
 * server, so what is checked here is what arrives in the file.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { Icon } from '../components/Icon';
import { Skeleton } from '../components/Skeleton';
import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { useToast } from '../hooks/useToast';
import { StockSyncApiError, api, ensureSession } from '../lib/api';
import { n } from '../lib/format';
import type {
  Report,
  ReportFormat,
  ReportHistoryPage,
  ReportKind,
  ReportPreview,
  ReportStatus,
} from '../types/api';

const KINDS: { key: ReportKind; label: string }[] = [
  { key: 'inventory', label: 'Inventory' },
  { key: 'sales', label: 'Sales' },
  { key: 'sku_performance', label: 'SKU performance' },
  // The Dashboard header exports this one directly. Offered here too, so the
  // Export Centre can reproduce every report it lists rather than showing a
  // kind only one screen knows how to make.
  { key: 'dashboard', label: 'Dashboard snapshot' },
  // Which imported SKUs Shopify actually sold. Unmatched first, because that
  // is the finding — a SKU the store has never sold under that spelling.
  { key: 'sku_matching', label: 'SKU matching' },
];

const FORMATS: { key: ReportFormat; label: string }[] = [
  { key: 'csv', label: 'CSV' },
  { key: 'xlsx', label: 'Excel' },
  { key: 'pdf', label: 'PDF' },
];

const RANGES: { key: string; label: string }[] = [
  { key: '7', label: 'Last 7 days' },
  { key: '30', label: 'Last 30 days' },
  { key: '90', label: 'Last 90 days' },
  { key: '365', label: 'Last 365 days' },
  { key: 'fy', label: 'This financial year' },
];

const FORMAT_LABELS: Record<ReportFormat, string> = {
  csv: 'CSV',
  xlsx: 'Excel',
  pdf: 'PDF',
};

/** A report is only worth polling while it is still being built. */
const POLL_MS = 900;

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function describe(report: Report): string {
  if (report.status === 'preparing') return 'Preparing…';
  if (report.status === 'failed') {
    return report.error_detail ?? 'Something went wrong while building this file.';
  }
  return `${report.range_label} · ${n(report.row_count)} rows · ${fileSize(report.size_bytes)}`;
}

export function ReportsPage() {
  const { toast } = useToast();

  const [kind, setKind] = useState<ReportKind>('inventory');
  const [range, setRange] = useState('30');
  // Off by default: an export is the copy people file and re-read, so it
  // carries every analysed SKU unless the top 50 is what was asked for.
  const [topOnly, setTopOnly] = useState(false);
  const [fmt, setFmt] = useState<ReportFormat>('csv');

  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const [history, setHistory] = useState<Report[] | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const message = (caught: unknown, fallback: string) =>
    caught instanceof StockSyncApiError ? caught.message : fallback;

  const loadPreview = useCallback(async () => {
    setPreview(null);
    setPreviewError(null);
    try {
      setPreview(
        await api.get<ReportPreview>(
          `/reports/preview?kind=${kind}&range_option=${range}&limit=12`,
        ),
      );
    } catch (caught) {
      setPreviewError(message(caught, 'Could not build a preview of this report.'));
    }
  }, [kind, range]);

  // What each report's status was last time we looked. The completion toast has
  // to come from here rather than from the POST response: with the threaded
  // worker that response is always "preparing", so keying off it meant the
  // toast only ever fired under the inline test runner.
  const lastStatus = useRef(new Map<number, ReportStatus>());

  const loadHistory = useCallback(async () => {
    try {
      const page = await api.get<ReportHistoryPage>('/reports?limit=20');
      const seen = lastStatus.current;
      for (const item of page.items) {
        const before = seen.get(item.id);
        if (before === 'preparing' && item.status === 'ready') {
          toast(`Report ready — ${item.filename}`, 'moss');
        } else if (before === 'preparing' && item.status === 'failed') {
          toast(item.error_detail ?? `Export failed — ${item.filename}`, 'rust', true);
        }
      }
      lastStatus.current = new Map(page.items.map((item) => [item.id, item.status]));
      setHistory(page.items);
    } catch {
      // The Export Centre is secondary to the page: failing to load it must not
      // take the preview down with it.
      setHistory([]);
    }
  }, [toast]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  // Poll only while something is actually preparing, and stop as soon as it
  // isn't — an idle Reports tab should not be talking to the server.
  const preparing = (history ?? []).some((report) => report.status === 'preparing');
  const historyRef = useRef(loadHistory);
  historyRef.current = loadHistory;

  useEffect(() => {
    if (!preparing) return;
    const timer = setInterval(() => void historyRef.current(), POLL_MS);
    return () => clearInterval(timer);
  }, [preparing]);

  const runExport = async () => {
    setExporting(true);
    try {
      const report = await api.post<Report>('/reports', {
        kind,
        fmt,
        range_option: range,
        top_only: topOnly,
      });
      // Seed the row's status so loadHistory can spot the transition to ready
      // and toast for it, however long the worker takes.
      lastStatus.current.set(report.id, report.status);
      setHistory((current) => [report, ...(current ?? [])]);
      await loadHistory();
    } catch (caught) {
      toast(message(caught, 'Export failed — try again.'), 'rust', true);
    } finally {
      setExporting(false);
    }
  };

  const download = async (report: Report) => {
    // A plain navigation rather than fetch+blob: the auth cookie rides along,
    // the browser handles the Content-Disposition filename, and a 50 MB export
    // never has to exist in a JS string first.
    //
    // The cost of that is no second chance — a navigation that 401s replaces
    // the page with an error envelope instead of downloading anything — so the
    // session is renewed first if it is close to expiring.
    if (!(await ensureSession())) return;
    window.location.assign(`/api/reports/${String(report.id)}/download`);
    toast(`Downloading ${report.filename}`, 'moss');
  };

  const remove = async (report: Report) => {
    setDeleting(report.id);
    try {
      await api.delete(`/reports/${String(report.id)}`);
      setHistory((current) => (current ?? []).filter((row) => row.id !== report.id));
      toast(`Deleted ${report.filename}`, 'slate');
    } catch (caught) {
      toast(message(caught, 'Could not delete that report.'), 'rust', true);
    } finally {
      setDeleting(null);
    }
  };

  const empty = preview !== null && preview.rows.length === 0;

  return (
    <Page>
      <PageHeader title="Reports" subtitle="Preview what you'll get, then export it" />

      <div className="toolbar">
        <span className="eyebrow">Report type</span>
        <div className="seg" role="group" aria-label="Report type">
          {KINDS.map((option) => (
            <button
              key={option.key}
              className={option.key === kind ? 'on' : ''}
              aria-pressed={option.key === kind}
              onClick={() => setKind(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>

        <span className="eyebrow" style={{ marginLeft: 10 }}>
          Range
        </span>
        <select
          className="inp sm"
          aria-label="Range"
          style={{ width: 'auto' }}
          value={range}
          onChange={(event) => setRange(event.target.value)}
        >
          {RANGES.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>

        <label className="check" style={{ marginLeft: 4 }}>
          <input
            type="checkbox"
            checked={topOnly}
            onChange={(event) => setTopOnly(event.target.checked)}
          />{' '}
          Export Top 50 only
        </label>

        <span className="spacer" />
        <span className="eyebrow">Export as</span>
        {FORMATS.map((option) => (
          <button
            key={option.key}
            className={`btn sm ${option.key === fmt ? 'pri' : ''}`}
            aria-pressed={option.key === fmt}
            onClick={() => setFmt(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="panel">
        <div className="p-hd">
          <h3>{preview?.title ?? 'Report'}</h3>
          {preview ? <span className="hint">{preview.subtitle}</span> : null}
          <div className="r">
            <span className="badge">{FORMAT_LABELS[fmt]} preview</span>
          </div>
        </div>

        {previewError ? (
          <div className="p-bd">
            <div className="inline-err">
              <Icon name="warn" />
              <div>{previewError}</div>
              <button className="btn sm" onClick={() => void loadPreview()}>
                <Icon name="refresh" size="s" /> Retry
              </button>
            </div>
          </div>
        ) : preview === null ? (
          <div className="p-bd" aria-busy="true">
            {[0, 1, 2, 3, 4].map((row) => (
              <Skeleton key={row} height={18} style={{ marginBottom: 12 }} />
            ))}
          </div>
        ) : empty ? (
          <div className="empty">
            <div className="ei">
              <Icon name="file" size="l" />
            </div>
            <h3>No data available to generate a report yet.</h3>
            <p>Import an inventory sheet and sync your store, then come back here.</p>
          </div>
        ) : (
          <div className="tbl-scroll" style={{ maxHeight: 460 }}>
            <table className="tbl">
              <thead>
                <tr>
                  {preview.columns.map((column) => (
                    <th key={column.header} className={column.align === 'right' ? 'n' : ''}>
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, index) => (
                  <tr key={`${row[0] ?? ''}-${String(index)}`}>
                    {row.map((cell, cellIndex) => (
                      <td
                        key={cellIndex}
                        className={preview.columns[cellIndex]?.align === 'right' ? 'n' : ''}
                      >
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="p-ft">
          <span className="hint">
            {preview && !empty
              ? `Showing the first ${String(preview.rows.length)} rows of the export.`
              : 'The export contains every row, not just the preview.'}
          </span>
          <span className="spacer" />
          <button className="btn cta" onClick={() => void runExport()} disabled={exporting}>
            {exporting ? (
              <>
                <span className="dot slate" /> Generating report…
              </>
            ) : (
              <>Export {FORMAT_LABELS[fmt]}</>
            )}
          </button>
        </div>
      </div>

      {history && history.length > 0 ? (
        <div className="panel">
          <div className="p-hd">
            <h3>Export centre</h3>
            <span className="hint">Reports stay here until you delete them</span>
          </div>
          <div className="p-bd">
            {history.map((report) => (
              <div className="linked" key={report.id}>
                <div className="li">
                  <Icon name="file" />
                </div>
                <div className="lt">
                  <b>{report.filename}</b>
                  <span>{describe(report)}</span>
                </div>

                {report.status === 'preparing' ? (
                  <span className="badge">Preparing</span>
                ) : report.status === 'failed' ? (
                  <span className="badge rust">Failed</span>
                ) : (
                  <>
                    <span className="badge moss">Ready</span>
                    <button className="btn sm" onClick={() => void download(report)}>
                      <Icon name="dl" size="s" /> Download
                    </button>
                  </>
                )}

                <button
                  className="btn sm dgr"
                  aria-label={`Delete ${report.filename}`}
                  disabled={deleting === report.id}
                  onClick={() => void remove(report)}
                >
                  <Icon name="trash" size="s" /> Delete
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </Page>
  );
}
