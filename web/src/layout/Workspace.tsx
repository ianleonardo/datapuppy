import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { useCallback, useEffect, useRef } from 'react';
import { put } from '../api/client';
import { useApp } from '../app/store';
import { ChatPanel } from '../features/chat/ChatPanel';
import { ErdPanel } from '../features/erd/ErdPanel';
import { RagPanel } from '../features/rag/RagPanel';
import { GridPanel } from '../features/results/GridPanel';
import { InterceptModal } from '../features/results/InterceptModal';
import { SchemaPanel } from '../features/schema/SchemaPanel';
import { SqlEditorPanel } from '../features/sql-editor/SqlEditorPanel';
import { GroupActions } from './GroupActions';
import { Header } from './Header';
import './workspace.css';

const components: Record<string, React.FC<IDockviewPanelProps>> = {
  chat: () => <ChatPanel />,
  rag: () => <RagPanel />,
  schema: () => <SchemaPanel />,
  erd: () => <ErdPanel />,
  sql: () => <SqlEditorPanel />,
  grid: () => <GridPanel />,
};

/** Saved layouts from an older default are discarded so everyone gets the current arrangement. */
const LAYOUT_VERSION = 3;

/**
 * Default layout (FS 3.2): left 40 % = one tab group References | Schema | Chat (Chat open);
 * right 60 % = SQL editor | ER diagram (top, SQL editor open) + Results (bottom).
 * Every group can be resized (drag the gaps), maximized, floated and re-docked by dragging its tabs.
 */
function buildDefault(api: DockviewApi) {
  api.clear();
  const total = api.width || window.innerWidth;
  const height = api.height || window.innerHeight - 64;
  api.addPanel({ id: 'rag', component: 'rag', title: 'References' });
  api.addPanel({
    id: 'schema',
    component: 'schema',
    title: 'Schema',
    position: { referencePanel: 'rag', direction: 'within' },
    inactive: true,
  });
  api.addPanel({
    id: 'chat',
    component: 'chat',
    title: 'Chat',
    position: { referencePanel: 'rag', direction: 'within' },
  });
  api.addPanel({
    id: 'sql',
    component: 'sql',
    title: 'SQL editor',
    position: { referencePanel: 'chat', direction: 'right' },
  });
  api.addPanel({
    id: 'erd',
    component: 'erd',
    title: 'ER diagram',
    position: { referencePanel: 'sql', direction: 'within' },
    inactive: true,
  });
  api.addPanel({
    id: 'grid',
    component: 'grid',
    title: 'Results',
    position: { referencePanel: 'sql', direction: 'below' },
  });
  api.getPanel('sql')?.api.setActive();
  api.getPanel('chat')?.api.setActive();
  try {
    api.getPanel('chat')!.group.api.setSize({ width: Math.round(total * 0.4) });
    api.getPanel('sql')!.group.api.setSize({ height: Math.round(height * 0.5) });
  } catch {
    /* sizing is best-effort */
  }
}

export default function Workspace() {
  const me = useApp((s) => s.me)!;
  const apiRef = useRef<DockviewApi>();
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();

  const onReady = useCallback(
    (e: DockviewReadyEvent) => {
      apiRef.current = e.api;
      const saved = me.preferences?.layout;
      let ok = false;
      if (saved?.v === LAYOUT_VERSION && saved.dock) {
        try {
          e.api.fromJSON(saved.dock);
          ok = ['chat', 'erd', 'grid'].every((id) => e.api.getPanel(id));
        } catch {
          ok = false;
        }
      }
      if (!ok) buildDefault(e.api);
      e.api.onDidLayoutChange(() => {
        clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(() => {
          put('/me/preferences', { layout: { v: LAYOUT_VERSION, dock: e.api.toJSON() } }).catch(
            () => undefined,
          );
        }, 1000); // debounced 1 s (TR-UI-02)
      });
    },
    [me.preferences],
  );

  const showPanel = useCallback((id: string) => {
    const api = apiRef.current;
    const p = api?.getPanel(id);
    if (!api || !p) return;
    // a maximized group would hide the panel we are switching to
    if (api.hasMaximizedGroup() && !p.group.api.isMaximized()) api.exitMaximizedGroup();
    p.api.setActive();
  }, []);

  useEffect(() => {
    const unsub = useApp.subscribe((s, prev) => {
      if (s.result !== prev.result && s.result) showPanel('grid');
      if (s.erdFocus !== prev.erdFocus && s.erd) showPanel('erd');
      if (s.editorSql !== prev.editorSql && s.editorSql) showPanel('sql');
    });
    return unsub;
  }, [showPanel]);

  // Double-click a tab to maximize / restore its group; Esc restores.
  useEffect(() => {
    const onDbl = (e: MouseEvent) => {
      const api = apiRef.current;
      const tab = (e.target as HTMLElement).closest('.dv-tab');
      if (!api || !tab) return;
      const grp = api.activeGroup;
      if (!grp || grp.api.location.type !== 'grid') return;
      if (grp.api.isMaximized()) grp.api.exitMaximized();
      else grp.api.maximize();
    };
    const onKey = (e: KeyboardEvent) => {
      const api = apiRef.current;
      if (e.key !== 'Escape' || !api?.hasMaximizedGroup()) return;
      if ((e.target as HTMLElement)?.closest?.('.monaco-editor, input, textarea')) return;
      if (
        document.querySelector('.modal-back, [role="dialog"], [data-radix-popper-content-wrapper]')
      )
        return;
      api.exitMaximizedGroup();
    };
    const el = document.querySelector('.ws-dock');
    el?.addEventListener('dblclick', onDbl as EventListener);
    window.addEventListener('keydown', onKey);
    return () => {
      el?.removeEventListener('dblclick', onDbl as EventListener);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  return (
    <div className="ws" data-theme={me.theme}>
      <Header onResetLayout={() => apiRef.current && buildDefault(apiRef.current)} />
      <div className="ws-dock">
        <DockviewReact
          components={components}
          onReady={onReady}
          rightHeaderActionsComponent={GroupActions}
          floatingGroupBounds="boundedWithinViewport"
          theme={{ name: 'sqa', className: 'dockview-theme-sqa', gap: 10 }}
        />
      </div>
      <InterceptModal />
    </div>
  );
}
