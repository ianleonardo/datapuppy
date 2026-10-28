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
import { Icon, IconButton } from '../../lib/icons';
import { HistoryPanel } from './HistoryPanel';

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

// Issues with name suggestions from the last check, for the editor's quick-fix (lightbulb / Ctrl+.) menu.
let quickFixIssues: any[] = [];
let quickFixRegistered = false;
function registerQuickFixes() {
  if (quickFixRegistered) return;
  quickFixRegistered = true;
  monaco.languages.registerCodeActionProvider('sql', {
    provideCodeActions(model, _range, context) {
      const actions: monaco.languages.CodeAction[] = [];
      for (const m of context.markers) {
        const i = quickFixIssues.find(
          (x) => x.line === m.startLineNumber && x.col === m.startColumn && x.suggestions?.length,
        );
        if (!i) continue;
        i.suggestions.forEach((name: string, k: number) =>
          actions.push({
            title: `Change to ${name}`,
            kind: 'quickfix',
            diagnostics: [m],
            isPreferred: k === 0,
            edit: {
              edits: [
                {
                  resource: model.uri,
                  versionId: model.getVersionId(),
                  textEdit: {
                    range: {
                      startLineNumber: m.startLineNumber,
                      endLineNumber: m.startLineNumber,
                      startColumn: m.startColumn,
                      endColumn: m.startColumn + i.token.length,
                    },
                    text: name,
                  },
                },
              ],
            },
          }),
        );
      }
      return { actions, dispose() {} };
    },
  });
}

const IS_MAC =
  typeof navigator !== 'undefined' &&
  /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const RUN_KEYS = IS_MAC ? '⌘↵' : 'Ctrl+↵';

export function SqlEditorPanel() {
  const me = useApp((s) => s.me)!;
  const theme = me.theme as ThemeName;
  const connectionId = useApp((s) => s.connectionId);
  const editorSql = useApp((s) => s.editorSql);
  const setComposer = useApp((s) => s.setComposer);
  const [sql, setSql] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const historyBtn = useRef<HTMLButtonElement>(null);
  const [issues, setIssues] = useState<any[]>([]);
  const [diff, setDiff] = useState<{ original: string; corrected: string; reply?: string } | null>(
    null,
  );
  const [checking, setChecking] = useState<'static' | 'ai' | null>(null);
  const [fixAll, setFixAll] = useState<string | null>(null); // corrected SQL from confident name fixes
  const autoSeq = useRef(0);
  const edRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const conn = me.connections.find((c) => c.id === connectionId);

  useEffect(() => {
    if (editorSql) setSql(editorSql.sql);
  }, [editorSql]);

  useEffect(() => {
    registerCompletion();
    registerQuickFixes();
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
    quickFixIssues = list;
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
    if (!sql.trim()) return toastError('Write a query first, or pick one from History.');
    setChecking(stage);
    useMood.getState().setMood('thinking');
    try {
      const res = await post('/queries/preflight', { connection_id: connectionId, sql, stage });
      setIssues(res.issues);
      applyMarkers(res.issues);
      setFixAll(
        res.corrected_sql && res.corrected_sql.trim() !== sql.trim() ? res.corrected_sql : null,
      );
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

  // Quiet static check while typing: unknown tables/columns get underlined with "did you mean" suggestions.
  useEffect(() => {
    if (!sql.trim()) {
      ++autoSeq.current; // drop any in-flight check
      setIssues([]);
      applyMarkers([]);
      setFixAll(null);
      return;
    }
    if (!connectionId || diff) return;
    const seq = ++autoSeq.current;
    const t = setTimeout(async () => {
      try {
        const res = await post('/queries/preflight', {
          connection_id: connectionId,
          sql,
          stage: 'static',
        });
        if (seq !== autoSeq.current) return; // a newer edit is already being checked
        setIssues(res.issues);
        applyMarkers(res.issues);
        setFixAll(
          res.corrected_sql && res.corrected_sql.trim() !== sql.trim() ? res.corrected_sql : null,
        );
      } catch {
        /* silent: the Check button reports errors */
      }
    }, 900);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sql, connectionId, diff]);

  /** Replace the identifier an issue points at with one of its suggestions (undoable in the editor). */
  function applySuggestion(i: any, name: string) {
    const ed = edRef.current;
    const model = ed?.getModel();
    if (!ed || !model || !i.token) return;
    const line = Math.min(Math.max(1, i.line), model.getLineCount());
    const text = model.getLineContent(line);
    let col = i.col;
    if (text.slice(col - 1, col - 1 + i.token.length).toLowerCase() !== i.token.toLowerCase()) {
      const at = text.toLowerCase().indexOf(i.token.toLowerCase());
      if (at < 0) return;
      col = at + 1;
    }
    ed.executeEdits('name-suggestion', [
      {
        range: new monaco.Range(line, col, line, col + i.token.length),
        text: name,
        forceMoveMarkers: true,
      },
    ]);
    ed.focus();
  }

  async function format() {
    if (!connectionId || !sql.trim()) return;
    const r = await post('/queries/format', { connection_id: connectionId, sql });
    setSql(r.sql);
  }

  function run(text: string) {
    if (!text.trim()) return toastError('Write a query first, or pick one from History.');
    runQuery({ sql: text, origin: 'MANUAL' });
  }

  /** Start an empty query (one undo step: Ctrl+Z brings the previous text back; runs stay in History). */
  function newQuery() {
    const ed = edRef.current;
    const model = ed?.getModel();
    setDiff(null);
    setIssues([]);
    setFixAll(null);
    setShowHistory(false);
    if (ed && model) {
      ed.pushUndoStop();
      ed.executeEdits('new-query', [{ range: model.getFullModelRange(), text: '' }]);
      ed.pushUndoStop();
      ed.focus();
    } else setSql('');
  }

  /** Load a query from History into the editor (one undo step, so Ctrl+Z brings the old text back). */
  function pickFromHistory(text: string) {
    const ed = edRef.current;
    const model = ed?.getModel();
    setDiff(null);
    if (ed && model) {
      ed.pushUndoStop();
      ed.executeEdits('history', [{ range: model.getFullModelRange(), text }]);
      ed.pushUndoStop();
      ed.focus();
    } else setSql(text);
    setShowHistory(false);
  }

  /** Text to run: the selection if there is one, otherwise the whole editor. */
  function runTarget(): string {
    const ed = edRef.current;
    const model = ed?.getModel();
    const sel = ed?.getSelection();
    if (ed && model && sel && !sel.isEmpty()) return model.getValueInRange(sel);
    return ed?.getValue() ?? sql;
  }

  const onMount: OnMount = (ed) => {
    edRef.current = ed as any;
    // Ctrl/Cmd+Enter or F5 runs the query (or just the selected part)
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => run(runTarget()));
    ed.addCommand(monaco.KeyCode.F5, () => run(runTarget()));
  };

  return (
    <div
      className="sqled"
      onKeyDown={(e) => {
        // same shortcuts when focus is on the toolbar or issue list (Monaco handles its own keys)
        if ((e.target as HTMLElement).closest('.monaco-editor')) return;
        if (e.key === 'F5' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) {
          e.preventDefault();
          run(runTarget());
        }
      }}
    >
      <div className="sqled-tools">
        <span className="pill pill-info">{conn ? conn.engine : 'no connection'}</span>
        <span className="ibtn-group">
          <IconButton label="New query" icon={<Icon.newFile />} onClick={newQuery} />
          <IconButton
            ref={historyBtn}
            label="History"
            icon={<Icon.history />}
            className={showHistory ? 'on' : ''}
            aria-expanded={showHistory}
            onClick={() => setShowHistory((v) => !v)}
          />
        </span>
        <span className="ibtn-group">
          <IconButton
            label={checking === 'static' ? 'Checking…' : 'Check names and syntax'}
            icon={checking === 'static' ? <Icon.spinner /> : <Icon.checkCircle />}
            onClick={() => check('static')}
            disabled={!!checking}
          />
          <IconButton
            label={checking === 'ai' ? 'Reviewing…' : 'AI review'}
            icon={checking === 'ai' ? <Icon.spinner /> : <Icon.sparkles />}
            onClick={() => check('ai')}
            disabled={!!checking}
          />
          <IconButton label="Format SQL" icon={<Icon.format />} onClick={format} />
        </span>
        <span className="grow" />
        <button
          className="btn btn-sm btn-ghost"
          onClick={() => setComposer('Check this SQL:\n```sql\n' + sql + '\n```')}
        >
          Send to chat
        </button>
        <button
          className="btn btn-sm btn-go"
          onClick={() => run(runTarget())}
          title={`Run the query, or only the selected part (${RUN_KEYS} or F5)`}
        >
          Run <kbd className="kbd">{RUN_KEYS}</kbd>
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
                setFixAll(null);
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
            keepCurrentOriginalModel
            keepCurrentModifiedModel
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
          {!sql && (
            <div className="sqled-placeholder" aria-hidden="true">
              Write a SELECT query here. {RUN_KEYS} or F5 runs it (or just the selected part).
              <br />
              Queries you ran before are under History.
            </div>
          )}
          <Editor
            height="100%"
            language="sql"
            theme={`sqa-${theme}`}
            value={sql}
            onChange={(v) => setSql(v || '')}
            onMount={onMount}
            options={{
              minimap: { enabled: false },
              fontFamily: 'JetBrains Mono',
              fontSize: 13.5,
              scrollBeyondLastLine: false,
              automaticLayout: true,
              wordWrap: 'on',
              tabSize: 2,
              overviewRulerLanes: 0, // no red error blocks in the scrollbar; squiggles mark errors
              overviewRulerBorder: false,
              hideCursorInOverviewRuler: true,
            }}
          />
        </div>
      )}
      {showHistory && (
        <HistoryPanel
          anchor={historyBtn.current}
          connectionId={connectionId}
          onPick={pickFromHistory}
          onClose={() => setShowHistory(false)}
        />
      )}
      {issues.length > 0 && !diff && (
        <ul className="issues sqled-issues">
          {issues.map((i, k) => (
            <li key={k} className={`issue sev-${i.severity}`}>
              <span className="issue-pos">
                L{i.line}:{i.col}
              </span>
              <span className="issue-msg">{i.message}</span>
              {i.suggestions?.length > 0 && (
                <span className="issue-fixes">
                  {i.suggestions.map((name: string) => (
                    <button
                      key={name}
                      className="chip chip-sm"
                      title={`Replace ${i.token} with ${name}`}
                      onClick={() => applySuggestion(i, name)}
                    >
                      {name}
                    </button>
                  ))}
                </span>
              )}
            </li>
          ))}
          {fixAll && (
            <li className="issue issue-fixall">
              <button
                className="btn btn-sm"
                onClick={() =>
                  setDiff({
                    original: sql,
                    corrected: fixAll,
                    reply:
                      'Fixes for keywords and names from the approved schema. Review before accepting.',
                  })
                }
              >
                Review all fixes
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
