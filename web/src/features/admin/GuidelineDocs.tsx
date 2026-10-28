import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, del, get, post } from '../../api/client';
import { Select } from '../../lib/Select';
import { toast, toastError } from '../../lib/toast';
import { DocViewer } from '../rag/DocViewer';
import { Card, Empty } from './kit';

const STATUS_PILL: Record<string, string> = {
  READY: 'pill-ok',
  UPLOADED: 'pill-info',
  EXTRACTING: 'pill-warn',
  EMBEDDING: 'pill-warn',
  FAILED: 'pill-bad',
  REJECTED: 'pill-bad',
};
const BUSY = ['UPLOADED', 'EXTRACTING', 'EMBEDDING'];
const fmtSize = (n: number) =>
  n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

/**
 * Guideline documents (Admin → Guidelines): policy/rule files that are chunked and embedded like references,
 * but retrieved into the AI's <guidelines> as rules for every country and database, one country or one database.
 */
export function GuidelineDocs({ countries, conns }: { countries: any[]; conns: any[] }) {
  const [docs, setDocs] = useState<any[]>([]);
  const [target, setTarget] = useState('all:');
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [viewing, setViewing] = useState<any | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(
    () =>
      get('/rag/documents?kind=GUIDELINE')
        .then((r) => setDocs(r.items))
        .catch(() => undefined),
    [],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!docs.some((d) => BUSY.includes(d.status))) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [docs, refresh]);

  const label = (d: any) =>
    d.scope === 'GLOBAL'
      ? 'All countries and databases'
      : d.scope === 'COUNTRY'
        ? `Country ${countries.find((k) => k.code === d.country_code)?.name ?? d.country_code}`
        : `Database ${conns.find((c) => c.id === d.connection_id)?.name ?? 'connection'}`;

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    const [t, v] = target.split(':');
    for (const f of Array.from(files)) {
      setUploading(f.name);
      const fd = new FormData();
      fd.append('file', f);
      fd.append('kind', 'GUIDELINE');
      fd.append('scope', t === 'k' ? 'COUNTRY' : t === 'c' ? 'CONNECTION' : 'GLOBAL');
      if (t === 'k') fd.append('country_code', v);
      if (t === 'c') fd.append('connection_id', v);
      try {
        await api('/rag/documents', { method: 'POST', body: fd });
        toast(`${f.name} uploaded. Reading and embedding it now…`);
      } catch (e) {
        toastError(`${f.name}: ${(e as ApiError).message}`);
      }
    }
    setUploading(null);
    if (fileRef.current) fileRef.current.value = '';
    refresh();
  }

  return (
    <div className="gdocs">
      <h3 className="gdocs-title">Guideline documents</h3>
      <p className="muted small">
        Upload policy or rule documents. They are split into passages and embedded; the passages
        most relevant to each question are given to the AI as guidelines it must follow.
      </p>
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
          <strong>{uploading ? `Uploading ${uploading}…` : 'Drop guideline files here'}</strong>
          <p className="muted small">PDF, DOCX, Markdown or TXT up to 50 MB.</p>
        </div>
        <div className="dz-actions">
          <Select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            aria-label="Applies to"
          >
            <option value="all:">All countries and databases</option>
            {countries.map((k) => (
              <option key={k.code} value={`k:${k.code}`}>
                Country {k.name}
              </option>
            ))}
            {conns.map((c) => (
              <option key={c.id} value={`c:${c.id}`}>
                Database {c.name}
              </option>
            ))}
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
      {docs.length === 0 ? (
        <Empty>No guideline documents yet.</Empty>
      ) : (
        <Card className="gdocs-card">
          <ul className="doclist">
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
                <span className="pill">{label(d)}</span>
                <span className={`pill ${STATUS_PILL[d.status] || ''}`}>
                  {d.status.toLowerCase()}
                </span>
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => setViewing(d)}
                  aria-label={`View ${d.filename}`}
                  title="View"
                >
                  👁
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => post(`/rag/documents/${d.id}/reindex`).then(refresh)}
                  title="Re-read and re-embed"
                  aria-label={`Re-embed ${d.filename}`}
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
              </li>
            ))}
          </ul>
        </Card>
      )}
      {viewing && <DocViewer doc={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
