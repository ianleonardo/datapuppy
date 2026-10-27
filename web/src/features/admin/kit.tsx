import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ApiError, get } from '../../api/client';
import { toastError } from '../../lib/toast';

export function useLoad<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    if (!path) return;
    get<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError((e as ApiError).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, reload, setData };
}

export async function act<T>(fn: () => Promise<T>, ok?: (r: T) => void): Promise<T | undefined> {
  try {
    const r = await fn();
    ok?.(r);
    return r;
  } catch (e) {
    toastError((e as ApiError).message || String(e));
    return undefined;
  }
}

export function Section({
  title,
  intro,
  actions,
  children,
}: {
  title: string;
  intro?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="adm-sec">
      <header className="adm-sec-head">
        <div>
          <h2>{title}</h2>
          {intro && <p className="muted">{intro}</p>}
        </div>
        <div className="adm-actions">{actions}</div>
      </header>
      {children}
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
  wide,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={`adm-field ${wide ? 'wide' : ''}`}>
      <span className="adm-label">{label}</span>
      {children}
      {hint && <span className="adm-hint">{hint}</span>}
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" aria-hidden="true">
        <span className="toggle-dot" />
      </span>
      <span>{label}</span>
    </label>
  );
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`adm-card ${className}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="adm-empty">{children}</p>;
}

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="modal-back"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
    >
      <div className={`modal ${wide ? 'modal-wide' : ''}`}>
        <div className="adm-modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export const fmtDate = (v: any) => (v ? String(v).replace('T', ' ').slice(0, 19) : '—');
export const fmtBytes = (n: number) =>
  n == null
    ? '—'
    : n > 1e9
      ? `${(n / 1e9).toFixed(1)} GB`
      : n > 1e6
        ? `${(n / 1e6).toFixed(1)} MB`
        : `${Math.round(n / 1e3)} KB`;
