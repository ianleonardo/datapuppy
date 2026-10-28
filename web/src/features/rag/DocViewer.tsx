import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { useEffect, useMemo, useState } from 'react';
import { ApiError, get } from '../../api/client';
import './docviewer.css';

const API = '/api/v1';

/** In-app viewer for an uploaded reference (Markdown rendered, PDF in the browser viewer). */
export function DocViewer({ doc, onClose }: { doc: any; onClose: () => void }) {
  const ext = (doc.filename.split('.').pop() || '').toLowerCase();
  const isPdf = ext === 'pdf';
  const [mode, setMode] = useState<'view' | 'text'>('view');
  const [data, setData] = useState<{ text: string; truncated: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (isPdf && mode === 'view') return;
    setErr(null);
    get(`/rag/documents/${doc.id}/text`)
      .then((r) => setData(r))
      .catch((e: ApiError) =>
        setErr(
          e.code === 'HTTP-404' || e.code === 'HTTP-405'
            ? 'The server is still running an older version that has no document viewer. Restart it (stop, then start) and try again.'
            : e.message,
        ),
      );
  }, [doc.id, isPdf, mode]);

  const html = useMemo(() => {
    if (!data || mode !== 'view' || !['md', 'markdown'].includes(ext)) return null;
    return DOMPurify.sanitize(marked.parse(data.text, { async: false, gfm: true }) as string);
  }, [data, mode, ext]);

  let body;
  if (err) body = <p className="adm-empty">{err}</p>;
  else if (isPdf && mode === 'view')
    body = (
      <iframe
        className="docv-frame"
        title={doc.filename}
        src={`${API}/rag/documents/${doc.id}/file`}
      />
    );
  else if (!data) body = <p className="muted">Fetching the document…</p>;
  else if (html) body = <article className="docv-md" dangerouslySetInnerHTML={{ __html: html }} />;
  else body = <pre className="docv-text">{data.text}</pre>;

  const canToggle = ['md', 'markdown', 'pdf'].includes(ext);
  return (
    <div
      className="modal-back"
      role="dialog"
      aria-modal="true"
      aria-label={doc.filename}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal docv">
        <div className="docv-head">
          <span className={`ref-ico ext-${ext}`} />
          <h3>{doc.filename}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close" autoFocus>
            ✕
          </button>
        </div>
        <div className="docv-bar">
          <span className="muted small">
            {doc.scope === 'PRIVATE' ? 'Private' : `Shared (${doc.scope.toLowerCase()})`} ·{' '}
            {doc.chunk_count ?? 0} passages indexed
          </span>
          <span className="grow" />
          {canToggle && (
            <div className="docv-seg" role="group" aria-label="View mode">
              <button className={mode === 'view' ? 'on' : ''} onClick={() => setMode('view')}>
                {isPdf ? 'Document' : 'Formatted'}
              </button>
              <button className={mode === 'text' ? 'on' : ''} onClick={() => setMode('text')}>
                {isPdf ? 'Extracted text' : 'Plain text'}
              </button>
            </div>
          )}
          <a
            className="btn btn-sm"
            href={`${API}/rag/documents/${doc.id}/file?download=true`}
            download={doc.filename}
          >
            Download
          </a>
        </div>
        {data?.truncated && mode !== 'view' && (
          <p className="muted small">Only the first 2 MB of text is shown.</p>
        )}
        <div className="docv-body">{body}</div>
      </div>
    </div>
  );
}
