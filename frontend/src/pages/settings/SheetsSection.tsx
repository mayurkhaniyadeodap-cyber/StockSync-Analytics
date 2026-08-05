/**
 * Settings → Google Sheets.
 *
 * The sheets this workspace imports from, with the two things you can do to
 * one you already have: run it again, or stop keeping it. Linking a sheet runs
 * an import — a link nothing has ever successfully read is not worth recording,
 * and the import is the only proof the sheet is readable.
 *
 * Every import here is the same fetcher, parser and upsert the Import page
 * uses. Nothing about importing lives in this file.
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Icon } from '../../components/Icon';
import { Skeleton } from '../../components/Skeleton';
import { useToast } from '../../hooks/useToast';
import { StockSyncApiError, api } from '../../lib/api';
import { freshness, n } from '../../lib/format';
import type { ImportResult, LinkedSheet, LinkedSheetList } from '../../types/api';

export function SheetsSection() {
  const navigate = useNavigate();
  const { toast } = useToast();

  const [sheets, setSheets] = useState<LinkedSheet[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | 'link' | null>(null);
  const [armed, setArmed] = useState<number | null>(null);

  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSheets((await api.get<LinkedSheetList>('/imports/sheets')).items);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof StockSyncApiError ? caught.message : "Couldn't load your sheets.",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const message = (caught: unknown, fallback: string) =>
    caught instanceof StockSyncApiError ? caught.message : fallback;

  async function link() {
    setBusy('link');
    setFormError(null);
    try {
      const result = await api.post<ImportResult>('/imports/sheets', {
        url: url.trim(),
        name: name.trim() || undefined,
      });
      setName('');
      setUrl('');
      await load();
      toast(`Imported ${n(result.batch.rows_imported)} SKUs from the sheet`, 'moss');
    } catch (caught) {
      // Inline rather than a toast: the link is still on screen and still
      // wrong, so the message belongs beside the field that has to change.
      setFormError(message(caught, "Couldn't import that sheet."));
    } finally {
      setBusy(null);
    }
  }

  async function resync(sheet: LinkedSheet) {
    setBusy(sheet.id);
    try {
      const result = await api.post<ImportResult>(`/imports/sheets/${String(sheet.id)}/resync`);
      toast(`${sheet.name}: imported ${n(result.batch.rows_imported)} SKUs`, 'moss');
    } catch (caught) {
      toast(message(caught, "Couldn't re-sync that sheet."), 'rust', true);
    } finally {
      // Either way the row's status has moved on.
      await load();
      setBusy(null);
    }
  }

  async function unlink(sheet: LinkedSheet) {
    if (armed !== sheet.id) {
      setArmed(sheet.id);
      return;
    }
    setBusy(sheet.id);
    try {
      await api.delete(`/imports/sheets/${String(sheet.id)}`);
      await load();
      toast(`${sheet.name} unlinked`, 'slate');
    } catch (caught) {
      toast(message(caught, "Couldn't unlink that sheet."), 'rust', true);
    } finally {
      setBusy(null);
      setArmed(null);
    }
  }

  return (
    <>
      <div className="panel">
        <div className="p-hd">
          <h3>Linked Google Sheets</h3>
          {sheets !== null && sheets.length > 0 && (
            <div className="r">
              <span className="badge">{n(sheets.length)} linked</span>
            </div>
          )}
        </div>

        {error ? (
          <div className="p-bd">
            <div className="inline-err">
              <Icon name="warn" />
              <div>{error}</div>
              <button className="btn sm" onClick={() => void load()}>
                <Icon name="refresh" size="s" /> Retry
              </button>
            </div>
          </div>
        ) : sheets === null ? (
          <div className="p-bd" aria-busy="true">
            <Skeleton height={38} />
            <Skeleton height={38} style={{ marginTop: 10 }} />
          </div>
        ) : sheets.length === 0 ? (
          <div className="empty">
            <div className="ei">
              <Icon name="sheet" size="l" />
            </div>
            <h3>No sheets linked yet</h3>
            <p>
              Link a Google Sheet to import it here whenever it changes, without downloading and
              uploading it again.
            </p>
          </div>
        ) : (
          <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Sheet</th>
                  <th>Last sync</th>
                  <th className="n">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sheets.map((sheet) => (
                  <tr key={sheet.id}>
                    <td data-l="Sheet">
                      <b>{sheet.name}</b>
                      <div className="help" style={{ marginTop: 2 }}>
                        <a href={sheet.url} target="_blank" rel="noreferrer noopener">
                          {sheet.url}
                        </a>
                      </div>
                    </td>
                    <td data-l="Last sync">
                      {sheet.last_synced_at ? (
                        <>
                          {freshness(new Date(sheet.last_synced_at))}
                          {sheet.last_status === 'failed' && (
                            <div
                              className="help"
                              style={{ marginTop: 2, color: 'var(--rust)' }}
                            >
                              Last import failed
                            </div>
                          )}
                        </>
                      ) : (
                        'Never'
                      )}
                    </td>
                    <td className="n" data-l="Actions">
                      <button
                        className="btn sm"
                        onClick={() => void resync(sheet)}
                        disabled={busy !== null}
                      >
                        {busy === sheet.id && armed !== sheet.id ? 'Syncing…' : 'Re-sync'}
                      </button>{' '}
                      <button
                        className={`btn sm dgr${armed === sheet.id ? ' armed' : ''}`}
                        onClick={() => void unlink(sheet)}
                        disabled={busy !== null && busy !== sheet.id}
                      >
                        {armed === sheet.id ? 'Confirm' : 'Unlink'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="p-hd">
          <h3>Link a new sheet</h3>
        </div>
        <div className="p-bd">
          <div className="field">
            <label htmlFor="sheet-name">Sheet name</label>
            <input
              id="sheet-name"
              className="inp"
              value={name}
              placeholder="Weekly stock count"
              onChange={(event) => {
                setName(event.target.value);
                setFormError(null);
              }}
            />
            <div className="help">What you call it. Optional — a label is used if blank.</div>
          </div>

          <div className="field">
            <label htmlFor="sheet-url">
              Google Sheet link <span className="req">*</span>
            </label>
            <input
              id="sheet-url"
              className={`inp${formError ? ' bad' : ''}`}
              value={url}
              placeholder="https://docs.google.com/spreadsheets/d/…"
              onChange={(event) => {
                setUrl(event.target.value);
                setFormError(null);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && url.trim() && busy === null) void link();
              }}
            />
            <div className="help">
              The sheet must be viewable by anyone with the link. Linking imports it now.
            </div>
          </div>

          {formError && (
            <div className="inline-err" style={{ marginTop: 12 }}>
              <Icon name="warn" />
              <div>{formError}</div>
            </div>
          )}
        </div>
        <div className="p-ft">
          <span className="hint">Imports run the same checks an upload does.</span>
          <span className="spacer" />
          <button className="btn sec" onClick={() => void navigate('/import')}>
            Import once instead
          </button>
          <button
            className="btn cta"
            onClick={() => void link()}
            disabled={!url.trim() || busy !== null}
          >
            {busy === 'link' ? 'Importing…' : 'Link new sheet'}
          </button>
        </div>
      </div>
    </>
  );
}
