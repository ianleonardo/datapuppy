import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { post, put } from '../api/client';
import { useApp } from '../app/store';
import { Mascot } from '../mascot/Mascot';
import { THEMES, applyTheme, type ThemeName } from '../theme/themes';
import { toast } from '../lib/toast';
import { Wordmark } from './Wordmark';

export function Header({ onResetLayout, title }: { onResetLayout?: () => void; title?: string }) {
  const me = useApp((s) => s.me)!;
  const setMe = useApp((s) => s.setMe);
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const theme = me.theme;
  const pinned = me.preferences?.pinned_theme as ThemeName | undefined;

  async function pin(t: ThemeName | '') {
    const res = await put('/me/preferences', { pinned_theme: t });
    const next = (t || theme) as ThemeName;
    applyTheme(next);
    setMe({ ...me, theme: next, preferences: res.preferences });
    toast(
      t ? `${THEMES[t].brand} theme pinned` : 'Theme will follow your clock from your next sign-in',
    );
  }
  async function logout() {
    await post('/auth/logout');
    location.reload();
  }
  const initials = me.user.display_name
    .split(/\s+/)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <header className="app-header">
      <div className="hdr-left">
        <button
          className="hdr-brand"
          onClick={() => setPage('workspace')}
          aria-label="Go to workspace"
        >
          <Wordmark theme={theme} />
        </button>
        {title && <span className="hdr-title">{title}</span>}
      </div>
      <div className="hdr-right">
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="avatar-btn" aria-label="Account menu">
              <span className="avatar">{initials}</span>
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content className="dd-content" align="end" sideOffset={8}>
              <DropdownMenu.Label className="dd-label">
                {me.user.display_name} ({me.user.user_id})
              </DropdownMenu.Label>
              {me.role === 'SYSTEM_ADMIN' && (
                <DropdownMenu.Item
                  className="dd-item"
                  onSelect={() => setPage(page === 'admin' ? 'workspace' : 'admin')}
                >
                  {page === 'admin' ? 'Back to workspace' : 'Administration'}
                </DropdownMenu.Item>
              )}
              {onResetLayout && (
                <DropdownMenu.Item className="dd-item" onSelect={onResetLayout}>
                  Reset layout
                </DropdownMenu.Item>
              )}
              <DropdownMenu.Separator className="dd-sep" />
              <DropdownMenu.Label className="dd-label">Theme</DropdownMenu.Label>
              {(Object.keys(THEMES) as ThemeName[]).map((t) => (
                <DropdownMenu.Item key={t} className="dd-item" onSelect={() => pin(t)}>
                  <Mascot theme={t} headOnly size={22} mood="idle" title="" />
                  Pin {THEMES[t].brand}
                  {pinned === t ? ' ✓' : ''}
                </DropdownMenu.Item>
              ))}
              {pinned && (
                <DropdownMenu.Item className="dd-item" onSelect={() => pin('')}>
                  Follow my clock
                </DropdownMenu.Item>
              )}
              <DropdownMenu.Separator className="dd-sep" />
              <DropdownMenu.Item
                className="dd-item"
                onSelect={() => setMe({ ...me, must_change_pw: true })}
              >
                Change password
              </DropdownMenu.Item>
              <DropdownMenu.Item className="dd-item" onSelect={logout}>
                Sign out
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </header>
  );
}
