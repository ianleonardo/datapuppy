import Editor, { DiffEditor, loader, type OnMount } from '@monaco-editor/react';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import { useEffect, useRef, useState } from 'react';
import { ApiError, get, post } from '../../api/client';
import { useApp } from '../../app/store';
import { runQuery } from '../../lib/exec';
import { toast, toastError } from '../../lib/toast';
import { useMood } from '../../mascot/moodStore';
import { THEMES, type ThemeName } from '../../theme/themes';

// Monaco bundled locally — no CDN loader (works air-gapped, TS §4).
(self as any).MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });
for (const [name, t] of Object.entries(THEMES)) {
  monaco.editor.defineTheme(`sqa-${name}`, {
    base: t.monaco.base,
    inherit: true,
    rules: t.monaco.rules,
    colors: t.monaco.colors,
  });
}

let completionNames: { tables: string[]; columns: Record<string, string[]> } = {
  tables: [],
  columns: {},
};
let completionRegistered = false;
function registerCompletion() {
  if (completionRegistered) return;
  completionRegistered = true;
  monaco.languages.registerCompletionItemProvider('sql', {
    triggerCharacters: ['.', ' '],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      const before = model.getLineContent(position.lineNumber).slice(0, word.startColumn - 1);
      const dot = before.match(/([A-Za-z_][\w]*)\.$/);
      if (dot) {
        const t = dot[1].toLowerCase();
        const cols = completionNames.columns[t] || Object.values(completionNames.columns).flat();
        return {
          suggestions: cols.map((c) => ({
            label: c,
            kind: monaco.languages.CompletionItemKind.Field,
            insertText: c,
            range,
          })),
        };
      }
      return {
        suggestions: [
          ...completionNames.tables.map((t) => ({
            label: t,
            kind: monaco.languages.CompletionItemKind.Struct,
            insertText: t,
            range,
          })),
          ...[...new Set(Object.values(completionNames.columns).flat())].map((c) => ({
            label: c,
            kind: monaco.languages.CompletionItemKind.Field,
            insertText: c,
            range,
          })),
        ],
      };
    },
  });
}

export function SqlEditorPanel() {
  const me = useApp((s) => s.me)!;
  const theme = me.theme as ThemeName;
  const connectionId = useApp((s) => s.connectionId);
  const editorSql = useApp((s) => s.editorSql);
  const setComposer = useApp((s) => s.setComposer);
  const [sql, setSql] = useState('SELECT *\nFROM orders\nLIMIT 50');
  const [issues, setIssues] = useState<any[]>([]);
  const [diff, setDiff] = useState<{ original: string; corrected: string; reply?: string } | null>(
    null,
  );
  const [checking, setChecking] = useState<'static' | 'ai' | null>(null);
  const edRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const conn = me.connections.find((c) => c.id === connectionId);

  useEffect(() => {
    if (editorSql) setSql(editorSql.sql);
  }, [editorSql]);

  useEffect(() => {
    registerCompletion();
    if (!connectionId) return;
    get('/schema/tree')
      .then(async (tree) => {
        const db = tree.countries
          .flatMap((c: any) => c.databases)
          .find((d: any) => d.id === connectionId);
        const tables = db ? db.schemas.flatMap((s: any) => s.tables) : [];
        completionNames = { tables: tables.map((t: any) => t.name), columns: {} };
        for (const t of tables.slice(0, 40)) {
          try {
            const d = await get(`/schema/${connectionId}/tables/${t.id}`);
            completionNames.columns[t.name.toLowerCase()] = d.columns.map(
              (c: any) => c.column_name,
            );
          } catch {
            /* ignore */
          }
        }
      })
      .catch(() => undefined);
  }, [connectionId]);

  function applyMarkers(list: any[]) {
    const model = edRef.current?.getModel();
    if (!model) return;
    monaco.editor.setModelMarkers(
      model,
      'preflight',
      list.map((i) => {
        const line = Math.min(Math.max(1, i.line || 1), model.getLineCount());
        const lineText = model.getLineContent(line);
        const startCol = Math.max(1, i.col || 1);
        const word = lineText.slice(startCol - 1).match(/^[\w.$"]+/);
        return {
          startLineNumber: line,
          endLineNumber: line,
          startColumn: startCol,
          endColumn: i.end_col || (word ? startCol + word[0].length : lineText.length + 1),
          message: i.message,
          severity:
            i.severity === 'error'
              ? monaco.MarkerSeverity.Error
              : i.severity === 'warning'
                ? monaco.MarkerSeverity.Warning
                : monaco.MarkerSeverity.Info,
        };
      }),
    );
  }

  async function check(stage: 'static' | 'ai') {
    if (!connectionId) return toastError('Pick a database connection first.');
    setChecking(stage);
    useMood.getState().setMood('thinking');
    try {
      const res = await post('/queries/preflight', { connection_id: connectionId, sql, stage });
      setIssues(res.issues);
      applyMarkers(res.issues);
      if (res.corrected_sql && res.corrected_sql.trim() !== sql.trim())
        setDiff({ original: sql, corrected: res.corrected_sql, reply: res.ai_reply });
      if (res.ai_error) toastError(res.ai_error);
      const errors = res.issues.filter((i: any) => i.severity === 'error').length;
      useMood.getState().setMood(res.destructive ? 'protective' : errors ? 'confused' : 'happy');
      if (!errors && !res.issues.length) toast('Looks clean. No issues found.');
    } catch (e) {
      toastError((e as ApiError).message);
      useMood.getState().setMood('confused');
    } finally {
      setChecking(null);
    }
  }

  async function format() {
    if (!connectionId) return;
    const r = await post('/queries/format', { connection_id: connectionId, sql });
    setSql(r.sql);
  }

  const onMount: OnMount = (ed) => {
    edRef.current = ed as any;
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () =>
      runQuery({ sql: ed.getValue(), origin: 'MANUAL' }),
    );
  };

  return (
    <div className="sqled">
      <div className="sqled-tools">
        <span className="pill pill-info">{conn ? conn.engine : 'no connection'}</span>
        <button className="btn btn-sm" onClick={() => check('static')} disabled={!!checking}>
          {checking === 'static' ? 'Checking…' : 'Check'}
        </button>
        <button
          className="btn btn-sm"
          onClick={() => check('ai')}
          disabled={!!checking}
          title="Ask the AI to review joins, predicates and grouping"
        >
          {checking === 'ai' ? 'Reviewing…' : 'AI review'}
        </button>
        <button className="btn btn-sm btn-ghost" onClick={format}>
          Format
        </button>
        <span className="grow" />
        <button
          className="btn btn-sm btn-ghost"
          onClick={() => setComposer('Check this SQL:\n```sql\n' + sql + '\n```')}
        >
          Send to chat
        </button>
        <button
          className="btn btn-sm btn-go"
          onClick={() => runQuery({ sql, origin: 'MANUAL' })}
          title="Run (Ctrl+Enter)"
        >
          Run
        </button>
      </div>
      {diff ? (
        <div className="sqled-diff">
          <div className="diff-bar">
            <span>{diff.reply || 'Suggested rewrite. Review the changes before accepting.'}</span>
            <span className="grow" />
            <button className="btn btn-sm" onClick={() => setDiff(null)}>
              Dismiss
            </button>
            <button
              className="btn btn-sm btn-go"
              onClick={() => {
                setSql(diff.corrected);
                setDiff(null);
                setIssues([]);
              }}
            >
              Accept rewrite
            </button>
          </div>
          <DiffEditor
            height="100%"
            language="sql"
            theme={`sqa-${theme}`}
            original={diff.original}
            modified={diff.corrected}
            options={{
              readOnly: true,
              renderSideBySide: false,
              minimap: { enabled: false },
              fontFamily: 'JetBrains Mono',
              fontSize: 13,
            }}
          />
        </div>
      ) : (
        <div className="sqled-editor">
          <Editor
            height="100%"
            language="sql"
            theme={`sqa-${theme}`}
            value={sql}
            onChange={(v) => {
              setSql(v || '');
              if (issues.length) {
                setIssues([]);
                applyMarkers([]);
              }
            }}
            onMount={onMount}
            options={{
              minimap: { enabled: false },
              fontFamily: 'JetBrains Mono',
              fontSize: 13.5,
              scrollBeyondLastLine: false,
              automaticLayout: true,
              wordWrap: 'on',
              tabSize: 2,
            }}
          />
        </div>
      )}
      {issues.length > 0 && !diff && (
        <ul className="issues sqled-issues">
          {issues.map((i, k) => (
            <li key={k} className={`issue sev-${i.severity}`}>
              <span className="issue-pos">
                L{i.line}:{i.col}
              </span>
              {i.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
