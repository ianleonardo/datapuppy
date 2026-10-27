import { Select } from '../../lib/Select';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, del, get, post } from '../../api/client';
import { useApp } from '../../app/store';
import { toast, toastError } from '../../lib/toast';
import { useMood } from '../../mascot/moodStore';
import { DocViewer } from './DocViewer';

const STATUS_PILL: Record<string, string> = {
  READY: 'pill-ok',
  UPLOADED: 'pill-info',
  EXTRACTING: 'pill-warn',
  EMBEDDING: 'pill-warn',
  FAILED: 'pill-bad',
  REJECTED: 'pill-bad',
};
const fmtSize = (n: number) =>
  n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

/** RAG reference upload & list (FS 2.1, TS §8). Analysts upload PRIVATE; admins can also upload shared scopes. */
export function RagPanel() {
  const me = useApp((s) => s.me)!;
  const connectionId = useApp((s) => s.connectionId);
  const isAdmin = me.role === 'SYSTEM_ADMIN';
  const [docs, setDocs] = useState<any[]>([]);
  const [scope, setScope] = useState('PRIVATE');
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [viewing, setViewing] = useState<any | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const conn = me.connections.find((c) => c.id === connectionId);

  const refresh = useCallback(
    () =>
      get('/rag/documents')
        .then((r) => setDocs(r.items))
        .catch(() => undefined),
    [],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!docs.some((d) => ['UPLOADED', 'EXTRACTING', 'EMBEDDING'].includes(d.status))) return;
    const t = setInterval(async () => {
      const before = docs.filter((d) => d.status !== 'READY').map((d) => d.id);
      const r = await get('/rag/documents');
      setDocs(r.items);
      if (r.items.some((d: any) => before.includes(d.id) && d.status === 'READY'))
        useMood.getState().setMood('happy');
    }, 1500);
    return () => clearInterval(t);
  }, [docs]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    for (const f of Array.from(files)) {
      setUploading(f.name);
      const fd = new FormData();
      fd.append('file', f);
      fd.append('scope', scope);
      if (scope === 'COUNTRY' && conn) fd.append('country_code', conn.country_code);
      if (scope === 'CONNECTION' && conn) fd.append('connection_id', conn.id);
      try {
        await api('/rag/documents', { method: 'POST', body: fd });
        toast(`${f.name} uploaded. Reading it now…`);
      } catch (e) {
        useMood.getState().setMood('confused');
        toastError(`${f.name}: ${(e as ApiError).message}`);
      }
    }
    setUploading(null);
    refresh();
  }

  return (
    <div className="rag">
      <div
        className={`dropzone ${drag ? 'drag' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          upload(e.dataTransfer.files);
        }}
      >
        <div>
          <strong>{uploading ? `Uploading ${uploading}…` : 'Drop reference files here'}</strong>
          <p className="muted small">
            PDF, DOCX, Markdown or TXT up to 50 MB. Text from these files is sent to the AI as
            context.
          </p>
        </div>
        <div className="dz-actions">
          <Select
            className="select"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            aria-label="Who can use this file"
          >
            <option value="PRIVATE">Just me</option>
            {isAdmin && <option value="GLOBAL">Everyone</option>}
            {isAdmin && conn && <option value="COUNTRY">Everyone in {conn.country_code}</option>}
            {isAdmin && conn && <option value="CONNECTION">Users of {conn.name}</option>}
          </Select>
          <button className="btn btn-primary btn-sm" onClick={() => fileRef.current?.click()}>
            Choose files
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            accept=".pdf,.docx,.md,.markdown,.txt"
            onChange={(e) => upload(e.target.files)}
          />
        </div>
      </div>
      <ul className="doclist">
        {docs.length === 0 && (
          <li className="muted small">
            No references yet. Glossaries, business rules and data dictionaries help most.
          </li>
        )}
        {docs.map((d) => (
          <li key={d.id} className="doc">
            <span className={`ref-ico ext-${d.filename.split('.').pop()}`} />
            <button
              className="doc-name doc-open"
              title={d.status_detail || `Open ${d.filename}`}
              onClick={() => setViewing(d)}
            >
              {d.filename}
            </button>
            <span className="muted small">{fmtSize(d.size_bytes)}</span>
            <span className="pill">
              {d.scope === 'PRIVATE' ? 'private' : d.scope.toLowerCase()}
            </span>
            <span className={`pill ${STATUS_PILL[d.status] || ''}`}>{d.status.toLowerCase()}</span>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => setViewing(d)}
              aria-label={`View ${d.filename}`}
              title="View"
            >
              👁
            </button>
            {(d.is_mine || isAdmin) && (
              <>
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => post(`/rag/documents/${d.id}/reindex`).then(refresh)}
                  title="Re-read and re-embed"
                >
                  ↻
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  aria-label={`Delete ${d.filename}`}
                  onClick={async () => {
                    await del(`/rag/documents/${d.id}`).catch((e) => toastError(e.message));
                    refresh();
                  }}
                >
                  🗑
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
      {viewing && <DocViewer doc={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
