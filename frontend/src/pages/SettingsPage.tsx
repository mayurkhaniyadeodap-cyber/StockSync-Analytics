/**
 * Design doc §13.
 *
 * Six sections, all live. Each configures something the rest of the app owns
 * and reads it from the same API that app uses — Settings is a place to change
 * things, never a second copy of them. Where a subject has its own page, this
 * shows a preview and a way through rather than a rival implementation.
 */

import type { ReactElement } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Page } from '../components/shell/Page';
import { PageHeader } from '../components/shell/PageHeader';
import { DisplaySection } from './settings/DisplaySection';
import { ImportsSection, SyncsSection } from './settings/HistorySections';
import { ProfileSection } from './settings/ProfileSection';
import { SheetsSection } from './settings/SheetsSection';
import { ShopifySection } from './settings/ShopifySection';

const SECTIONS = [
  { key: 'shopify', label: 'Shopify' },
  { key: 'sheets', label: 'Google Sheets' },
  { key: 'profile', label: 'Profile' },
  { key: 'prefs', label: 'Display' },
  { key: 'imports', label: 'Import history' },
  { key: 'syncs', label: 'Sync history' },
] as const;

type SectionKey = (typeof SECTIONS)[number]['key'];

const PANELS: Record<SectionKey, () => ReactElement | null> = {
  shopify: ShopifySection,
  sheets: SheetsSection,
  profile: ProfileSection,
  prefs: DisplaySection,
  imports: ImportsSection,
  syncs: SyncsSection,
};

export function SettingsPage() {
  const { section } = useParams<{ section: string }>();
  const navigate = useNavigate();

  const active = (SECTIONS.find((s) => s.key === section)?.key ?? 'prefs') as SectionKey;
  const Panel = PANELS[active];

  return (
    <Page>
      <PageHeader
        title="Settings"
        subtitle="Changes save as you go — each field confirms on its own"
      />

      <div className="set-grid">
        <div className="set-nav">
          {SECTIONS.map((s) => (
            <button
              key={s.key}
              className={s.key === active ? 'on' : ''}
              onClick={() => navigate(`/settings/${s.key}`)}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div>
          <Panel />
        </div>
      </div>
    </Page>
  );
}
