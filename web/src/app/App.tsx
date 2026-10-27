import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { ApiError, get, onAuthError, setCsrf } from '../api/client';
import { ChangePasswordPage, LoginPage } from '../features/auth/LoginPage';
import { Toasts } from '../lib/toast';
import { startActivityTracking, useMood } from '../mascot/moodStore';
import { applyTheme, type ThemeName } from '../theme/themes';
import { useApp } from './store';

const Workspace = lazy(() => import('../layout/Workspace'));
const AdminPage = lazy(() => import('../features/admin/AdminPage'));

export function App() {
  const [phase, setPhase] = useState<'loading' | 'login' | 'ready'>('loading');
  const me = useApp((s) => s.me);
  const setMe = useApp((s) => s.setMe);
  const page = useApp((s) => s.page);

  const load = useCallback(async () => {
    try {
      const m = await get('/me');
      setCsrf(m.csrf_token);
      applyTheme(m.theme as ThemeName);
      setMe(m);
      setPhase('ready');
      useMood.getState().setMood('waking');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setPhase('login');
      else setPhase('login');
    }
  }, [setMe]);

  useEffect(() => {
    load();
    const off = onAuthError(() => {
      setMe(null);
      setPhase('login');
    });
    const stop = startActivityTracking();
    return () => {
      off();
      stop();
    };
  }, [load, setMe]);

  if (phase === 'loading') return <div style={{ height: '100%', background: 'var(--bg)' }} />;
  if (phase === 'login' || !me)
    return (
      <>
        <LoginPage onLoggedIn={load} />
        <Toasts />
      </>
    );
  if (me.must_change_pw) return <ChangePasswordPage theme={me.theme} forced onDone={load} />;
  return (
    <>
      <Suspense fallback={<div style={{ height: '100%', background: 'var(--bg)' }} />}>
        {page === 'admin' && me.role === 'SYSTEM_ADMIN' ? <AdminPage /> : <Workspace />}
      </Suspense>
      <Toasts />
    </>
  );
}
