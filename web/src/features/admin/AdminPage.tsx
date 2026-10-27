import { useState } from 'react';
import { useApp } from '../../app/store';
import { Header } from '../../layout/Header';
import { Mascot } from '../../mascot/Mascot';
import { AuditSection, ChatMetaSection, PurgeSection, SystemSection } from './OpsSections';
import { ConnectionsSection, CountriesSection, GuidelinesSection } from './DataSections';
import { LlmSection, SettingsSection, StartersSection } from './LlmSettingsSections';
import { SchemaSection } from './SchemaSection';
import { AuthSection, UsersSection } from './UsersSections';
import '../../layout/workspace.css';
import './admin.css';

const SECTIONS = [
  { id: 'users', label: 'Users & roles', C: UsersSection },
  { id: 'auth', label: 'Authentication', C: AuthSection },
  { id: 'countries', label: 'Countries', C: CountriesSection },
  { id: 'connections', label: 'Connections', C: ConnectionsSection },
  { id: 'schema', label: 'Schema governance', C: SchemaSection },
  { id: 'guidelines', label: 'Guidelines', C: GuidelinesSection },
  { id: 'llm', label: 'LLM providers', C: LlmSection },
  { id: 'settings', label: 'Settings', C: SettingsSection },
  { id: 'starters', label: 'Starter prompts', C: StartersSection },
  { id: 'system', label: 'System', C: SystemSection },
  { id: 'audit', label: 'Audit log', C: AuditSection },
  { id: 'chatmeta', label: 'Chat activity', C: ChatMetaSection },
  { id: 'purge', label: 'Purge', C: PurgeSection },
] as const;

export default function AdminPage() {
  const me = useApp((s) => s.me)!;
  const [sec, setSec] = useState<string>(() => sessionStorage.getItem('admin-sec') || 'users');
  const Active = SECTIONS.find((s) => s.id === sec)?.C ?? UsersSection;
  return (
    <div className="ws admin" data-theme={me.theme}>
      <Header title="Administration" />
      <div className="admin-body">
        <nav className="admin-nav" aria-label="Admin sections">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className={s.id === sec ? 'on' : ''}
              aria-current={s.id === sec ? 'page' : undefined}
              onClick={() => {
                setSec(s.id);
                try {
                  sessionStorage.setItem('admin-sec', s.id);
                } catch {
                  /* ignore */
                }
              }}
            >
              {s.label}
            </button>
          ))}
          <div className="admin-pup">
            <Mascot theme={me.theme} mood="idle" size={110} />
          </div>
        </nav>
        <main className="admin-main">
          <Active />
        </main>
      </div>
    </div>
  );
}
