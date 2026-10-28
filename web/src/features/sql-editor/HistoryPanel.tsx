import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { get } from '../../api/client';
import { useApp } from '../../app/store';
import { HighlightedSql } from '../../lib/sql';

const STATUS: Record<string, [string, string]> = {
  SUCCEEDED: ['pill-ok', 'ok'],
  FAILED: ['pill-bad', 'failed'],
  REJECTED: ['pill-bad', 'blocked'],
  CANCELLED: ['pill-warn', 'cancelled'],
  INTERCEPTED: ['pill-warn', 'held'],
  RUNNING: ['pill-info', 'running'],
};

function ago(iso?: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString();
}

/**
 * SQL editor history: queries you ran (from the editor or from chat), newest first, one entry per distinct query.
 * Click an entry to load it into the editor.
 */
export function HistoryPanel({
  anchor,
  connectionId,
  onPick,
  onClose,
}: {
  anchor: HTMLElement | null;
  connectionId: string | null;
  onPick: (sql: string, item: any) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState('');
  const [thisDb, setThisDb] = useState(true);
  const [origin, setOrigin] = useState<'' | 'MANUAL' | 'AI'>('');
  const [items, setItems] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const lastResult = useApp((s) => s.result?.run_id);
  const searchRef = useRef<HTMLInputElement>(null);

  const panelRef = useRef<HTMLElement>(null);
  const [box, setBox] = useState<{
    top: number;
    left: number;
    width: number;
    height: number;
  } | null>(null);

  // Pop-over under the History button, as tall as the window allows (not limited to the editor panel).
  useLayoutEffect(() => {
    const place = () => {
      const r = anchor?.getBoundingClientRect();
      const width = Math.min(500, window.innerWidth - 24);
      const top = r ? r.bottom + 8 : 80;
      const left = Math.max(12, Math.min((r?.left ?? 12) - 8, window.innerWidth - width - 12));
      setBox({ top, left, width, height: Math.max(260, window.innerHeight - top - 16) });
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [anchor]);

  useEffect(() => {
    searchRef.current?.focus();
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panelRef.current?.contains(t) && !anchor?.contains(t)) onClose();
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', esc);
    };
  }, [anchor, onClose]);

  useEffect(() => {
    const t = setTimeout(() => {
      const p = new URLSearchParams({ limit: '100' });
      if (q.trim()) p.set('q', q.trim());
      if (thisDb && connectionId) p.set('connection_id', connectionId);
      if (origin) p.set('origin', origin);
      get(`/queries/history?${p}`)
        .then((r) => {
          setItems(r.items);
          setErr(null);
        })
        .catch((e) => setErr(e.message));
    }, 200);
    return () => clearTimeout(t);
  }, [q, thisDb, origin, connectionId, lastResult]);

  const theme = document.querySelector('.ws')?.getAttribute('data-theme') ?? undefined;
  return createPortal(
    <aside
      ref={panelRef}
      className="sqh"
      data-theme={theme}
      aria-label="Query history"
      style={
        box ? { top: box.top, left: box.left, width: box.width, height: box.height } : undefined
      }
    >
      <div className="sqh-head">
        <strong>History</strong>
        <span className="grow" />
        <button className="icon-btn" onClick={onClose} aria-label="Close history">
          ✕
        </button>
      </div>
      <div className="sqh-filters">
        <input
          ref={searchRef}
          className="input sqh-search"
          placeholder="Search your queries…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search history"
        />
        <div className="sqh-toggles">
          <div className="docv-seg" role="group" aria-label="Who wrote it">
            {(
              [
                ['', 'All'],
                ['MANUAL', 'Mine'],
                ['AI', 'From chat'],
              ] as const
            ).map(([v, label]) => (
              <button key={v} className={origin === v ? 'on' : ''} onClick={() => setOrigin(v)}>
                {label}
              </button>
            ))}
          </div>
          <label className="sqh-check">
            <input type="checkbox" checked={thisDb} onChange={(e) => setThisDb(e.target.checked)} />
            This database only
          </label>
        </div>
      </div>
      <ul className="sqh-list">
        {err && <li className="sqh-empty">{err}</li>}
        {!err && items === null && <li className="sqh-empty">Loading…</li>}
        {!err && items?.length === 0 && (
          <li className="sqh-empty">
            {q ? 'No queries match your search.' : 'Queries you run will show up here.'}
          </li>
        )}
        {items?.map((it) => {
          const [cls, label] = STATUS[it.status] || ['', it.status.toLowerCase()];
          return (
            <li key={it.id}>
              <button
                className="sqh-item"
                onClick={() => onPick(it.sql_text, it)}
                title="Load into the editor"
              >
                <pre className="sqh-sql">
                  <HighlightedSql sql={it.sql_text} />
                </pre>
                <span className="sqh-meta">
                  <span className={`pill ${cls}`}>{label}</span>
                  {it.status === 'SUCCEEDED' && it.row_count != null && (
                    <span>{Number(it.row_count).toLocaleString()} rows</span>
                  )}
                  {it.duration_ms != null && <span>{(it.duration_ms / 1000).toFixed(1)} s</span>}
                  <span>{it.origin === 'MANUAL' ? 'editor' : 'chat'}</span>
                  {it.times_run > 1 && <span>×{it.times_run}</span>}
                  {!thisDb && it.connection_name && <span>{it.connection_name}</span>}
                  <span className="grow" />
                  <span title={it.ran_at ? new Date(it.ran_at).toLocaleString() : ''}>
                    {ago(it.ran_at)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>,
    document.body,
  );
}
