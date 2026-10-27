import { useEffect, useRef, useState } from 'react';
import { ApiError, get, post, setCsrf } from '../../api/client';
import { Mascot, type Mood } from '../../mascot/Mascot';
import { THEMES, applyTheme, themeForHour, type ThemeName } from '../../theme/themes';
import { Wordmark } from '../../layout/Wordmark';
import { Scene } from './scenes';
import './login.css';

const TAGLINES: Record<ThemeName, string> = {
  morning: "Ask in plain words. I'll fetch the SQL.",
  afternoon: 'Chase down any insight, even the sun.',
  night: 'Quiet databases, bright answers.',
};

export function LoginPage({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [theme, setTheme] = useState<ThemeName>(() => {
    const q = new URLSearchParams(location.search).get('theme') as ThemeName | null;
    return q && q in THEMES ? q : themeForHour(new Date().getHours());
  });
  const [cfg, setCfg] = useState<any>(null);
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mood, setMood] = useState<Mood>(theme === 'night' ? 'sleepy' : 'waking');
  const [cover, setCover] = useState(false);
  const moodTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    get('/auth/config')
      .then((c) => {
        setCfg(c);
        if (c.theme_windows && !new URLSearchParams(location.search).get('theme'))
          setTheme(themeForHour(new Date().getHours(), c.theme_windows));
      })
      .catch(() => setCfg({ local_enabled: true }));
  }, []);
  useEffect(() => {
    if (mood === 'waking') {
      moodTimer.current = setTimeout(() => setMood('idle'), 1900);
      return () => clearTimeout(moodTimer.current);
    }
  }, [mood]);

  const nudge = (m: Mood) => {
    if (mood === 'sleepy') setMood('waking');
    else if (m !== mood) setMood(m);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCover(false);
    setMood('thinking');
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const res = await post('/auth/login', { user_id: userId.trim(), password, tz });
      setCsrf(res.csrf_token);
      setMood('happy');
      applyTheme(res.theme);
      setTimeout(onLoggedIn, 650);
    } catch (err) {
      const e2 = err as ApiError;
      setMood(e2.code === 'E-AUTH-005' ? 'protective' : 'confused');
      setError(e2.message || 'Sign-in failed.');
      setBusy(false);
    }
  }

  const t = THEMES[theme];
  return (
    <div className="login" data-theme={theme}>
      <Scene theme={theme} />
      <main className="login-stage">
        <section className="login-card" aria-labelledby="login-title">
          <Wordmark theme={theme} size="lg" />
          <p className="login-tag">{TAGLINES[theme]}</p>
          <form onSubmit={submit} className="login-form" noValidate>
            <label className="fld">
              <span>User ID</span>
              <input
                id="login-title"
                autoComplete="username"
                value={userId}
                onChange={(e) => {
                  setUserId(e.target.value);
                  nudge('idle');
                }}
                required
                autoFocus
              />
            </label>
            <label className="fld">
              <span>Password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onFocus={() => {
                  if (mood === 'sleepy') setMood('waking');
                  setCover(true);
                }}
                onBlur={() => setCover(false)}
                required
              />
            </label>
            {error && (
              <p className="login-error" role="alert">
                {error}
              </p>
            )}
            <button className="btn-signin" disabled={busy || !userId || !password}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
            {cfg?.ldap_enabled && (
              <p className="login-note">Use your network (LDAP) account or a local account.</p>
            )}
            <p className="login-credit">An application by Ian Leonardo, 2026</p>
          </form>
        </section>
        <div className="login-pup">
          <Mascot theme={theme} mood={mood} coverEyes={cover} size="100%" />
        </div>
      </main>
      <nav className="theme-peek" aria-label="Preview themes">
        {(Object.keys(THEMES) as ThemeName[]).map((n) => (
          <button
            key={n}
            className={n === theme ? 'on' : ''}
            aria-pressed={n === theme}
            onClick={() => {
              setTheme(n);
              setMood(n === 'night' ? 'sleepy' : 'waking');
            }}
          >
            {THEMES[n].brand}
          </button>
        ))}
      </nav>
      <span className="sr-only">{t.brand}</span>
    </div>
  );
}

export function ChangePasswordPage({
  theme,
  forced,
  onDone,
}: {
  theme: ThemeName;
  forced: boolean;
  onDone: () => void;
}) {
  const [cur, setCur] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [mood, setMood] = useState<Mood>('idle');
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) {
      setErr("The new passwords don't match.");
      setMood('confused');
      return;
    }
    try {
      await post('/auth/change-password', { current_password: cur, new_password: pw });
      setMood('happy');
      setTimeout(onDone, 600);
    } catch (e2) {
      setErr((e2 as ApiError).message);
      setMood('confused');
    }
  }
  return (
    <div className="login" data-theme={theme}>
      <Scene theme={theme} />
      <main className="login-stage">
        <section className="login-card">
          <Wordmark theme={theme} size="lg" />
          <p className="login-tag">
            {forced ? 'Pick a new password before we start.' : 'Change your password.'}
          </p>
          <form onSubmit={submit} className="login-form">
            <label className="fld">
              <span>Current password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={cur}
                onChange={(e) => setCur(e.target.value)}
              />
            </label>
            <label className="fld">
              <span>New password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={pw}
                onChange={(e) => setPw(e.target.value)}
              />
            </label>
            <label className="fld">
              <span>Repeat new password</span>
              <input
                type="password"
                autoComplete="new-password"
                value={pw2}
                onChange={(e) => setPw2(e.target.value)}
              />
            </label>
            <p className="login-note">
              At least 12 characters, using 3 of: lowercase, uppercase, digit, symbol. Don't include
              your user ID.
            </p>
            {err && (
              <p className="login-error" role="alert">
                {err}
              </p>
            )}
            <button className="btn-signin" disabled={!cur || !pw}>
              Save password
            </button>
          </form>
        </section>
        <div className="login-pup">
          <Mascot theme={theme} mood={mood} size="100%" />
        </div>
      </main>
    </div>
  );
}
