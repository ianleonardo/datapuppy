import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { useMemo } from 'react';
import { useApp } from '../../app/store';
import { runQuery } from '../../lib/exec';
import { HighlightedSql } from '../../lib/sql';
import { copyText } from '../../lib/toast';

export type Block = {
  type: string;
  content?: { type: string; text: string }[];
  props?: Record<string, any>;
};

export function Markdown({ text }: { text: string }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(text, { async: false, breaks: true }) as string),
    [text],
  );
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

function SqlCard({
  p,
  messageId,
  sessionId,
}: {
  p: Record<string, any>;
  messageId?: string;
  sessionId?: string | null;
}) {
  const openInEditor = useApp((s) => s.openInEditor);
  const setComposer = useApp((s) => s.setComposer);
  const busy = useApp((s) => s.resultBusy);
  return (
    <div className="sqlcard">
      <div className="sqlcard-head">
        <span className="pill pill-info">{p.dialect}</span>
        {p.origin === 'REFINED' && <span className="pill pill-warn">Suggested rewrite</span>}
        <span className="grow" />
        <button className="btn btn-sm btn-ghost" onClick={() => openInEditor(p.sql)}>
          Open in editor
        </button>
        <button className="btn btn-sm" onClick={() => copyText(p.sql, 'SQL copied')}>
          Copy
        </button>
      </div>
      <pre className="sqlcode">
        <code>
          <HighlightedSql sql={p.sql} />
        </code>
      </pre>
      {p.note && <p className="sqlcard-note">{p.note}</p>}
      {p.assumptions?.length > 0 && (
        <ul className="assumptions">
          {p.assumptions.map((a: string, i: number) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      )}
      {p.citations?.length > 0 && (
        <p className="sqlcard-note">From your references: {[...new Set(p.citations)].join(', ')}</p>
      )}
      <div className="sqlcard-actions">
        <button
          className="btn btn-go"
          disabled={busy}
          onClick={() =>
            runQuery({
              sql: p.sql,
              origin: p.origin === 'REFINED' ? 'REFINED' : 'AI',
              message_id: messageId,
              session_id: sessionId,
            })
          }
        >
          Execute <span aria-hidden="true">🚀</span>
        </button>
        <button className="btn btn-refine" onClick={() => setComposer('Refine the last query: ')}>
          Refine <span aria-hidden="true">🛠️</span>
        </button>
      </div>
    </div>
  );
}

export function MermaidBlock({ p }: { p: Record<string, any> }) {
  const setErd = useApp((s) => s.setErd);
  return (
    <div className="mini-card">
      <div className="mini-card-row">
        <strong>{p.title || 'Diagram'}</strong>
        <span className="grow" />
        <button className="btn btn-sm" onClick={() => setErd({ source: p.source, title: p.title })}>
          Show in ER panel
        </button>
        <button
          className="btn btn-sm btn-ghost"
          onClick={() => copyText(p.source, 'Mermaid source copied')}
        >
          Copy source
        </button>
      </div>
    </div>
  );
}

function Insight({ p }: { p: Record<string, any> }) {
  return (
    <div className="insight">
      <div className="insight-badges">
        <span className="pill pill-info">{p.rows ?? '?'} rows</span>
        <span className="pill">{p.cols} columns</span>
        {p.method && p.method !== 'COUNT' && (
          <span className="pill pill-warn">{p.method.toLowerCase()} count</span>
        )}
      </div>
      {p.insights?.length > 0 && (
        <ul className="insight-list">
          {p.insights.map((t: string, i: number) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
      {p.anomalies?.length > 0 && (
        <>
          <p className="insight-sub">Worth a closer look</p>
          <ul className="insight-list warn">
            {p.anomalies.map((t: string, i: number) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function Chips({ p, onAsk }: { p: Record<string, any>; onAsk: (t: string) => void }) {
  return (
    <div className="chips">
      {p.chips.map((c: any, i: number) => (
        <button key={i} className="chip" onClick={() => onAsk(c.prompt)}>
          {c.label}
        </button>
      ))}
    </div>
  );
}

function Review({ p }: { p: Record<string, any> }) {
  if (!p.issues?.length) return null;
  return (
    <ul className="issues">
      {p.issues.map((i: any, k: number) => (
        <li key={k} className={`issue sev-${i.severity}`}>
          <span className="issue-pos">
            L{i.line}:{i.col}
          </span>
          {i.message}
        </li>
      ))}
    </ul>
  );
}

function Hygiene({ p }: { p: Record<string, any> }) {
  return (
    <table className="hygiene">
      <thead>
        <tr>
          <th>Table</th>
          <th>Why it smells</th>
          <th>Suggestion</th>
        </tr>
      </thead>
      <tbody>
        {p.flagged.map((f: any) => (
          <tr key={f.table}>
            <td className="mono">{f.table}</td>
            <td>{f.reason}</td>
            <td>{f.recommendation}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function BlockView({
  b,
  messageId,
  sessionId,
  onAsk,
}: {
  b: Block;
  messageId?: string;
  sessionId?: string | null;
  onAsk: (t: string) => void;
}) {
  switch (b.type) {
    case 'paragraph':
      return <Markdown text={(b.content || []).map((c) => c.text).join('')} />;
    case 'sqlCard':
      return <SqlCard p={b.props!} messageId={messageId} sessionId={sessionId} />;
    case 'mermaid':
      return <MermaidBlock p={b.props!} />;
    case 'insight':
      return <Insight p={b.props!} />;
    case 'recommendationChips':
      return <Chips p={b.props!} onAsk={onAsk} />;
    case 'review':
      return <Review p={b.props!} />;
    case 'hygiene':
      return <Hygiene p={b.props!} />;
    default:
      return null;
  }
}

export function blocksToMarkdown(blocks: Block[]): string {
  return blocks
    .map((b) => {
      if (b.type === 'paragraph') return (b.content || []).map((c) => c.text).join('');
      if (b.type === 'sqlCard') return '```sql\n' + b.props!.sql + '\n```';
      if (b.type === 'mermaid') return '```mermaid\n' + b.props!.source + '\n```';
      if (b.type === 'insight')
        return (b.props!.insights || []).map((i: string) => `- ${i}`).join('\n');
      return '';
    })
    .filter(Boolean)
    .join('\n\n');
}
