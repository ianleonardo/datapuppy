import { create } from 'zustand';
import type { ThemeName } from '../theme/themes';

export interface Me {
  user: { id: string; user_id: string; display_name: string; role: string };
  role: 'SYSTEM_ADMIN' | 'ANALYST';
  theme: ThemeName;
  csrf_token: string;
  connections: { id: string; name: string; country_code: string; engine: string }[];
  preferences: Record<string, any>;
  must_change_pw: boolean;
  ui: {
    mascot_enabled: boolean;
    page_size_default: number;
    page_size_options: number[];
    max_rows: number;
    max_cols: number;
  };
}

export interface ResultState {
  run_id: string;
  columns: { name: string; type: string }[];
  rows: any[][];
  page: number;
  page_size: number;
  total_rows: number | null;
  total_pages: number | null;
  row_count_method: string;
  sql?: string;
  duration_ms?: number;
  tables?: string[];
  connection_id: string;
}

interface AppState {
  me: Me | null;
  setMe: (m: Me | null) => void;
  page: 'workspace' | 'admin';
  setPage: (p: 'workspace' | 'admin') => void;
  connectionId: string | null;
  setConnectionId: (id: string | null) => void;
  sessionId: string | null;
  setSessionId: (id: string | null) => void;
  result: ResultState | null;
  setResult: (r: ResultState | null) => void;
  resultBusy: boolean;
  setResultBusy: (b: boolean) => void;
  erd: { source: string; title?: string } | null;
  /** focus=false updates the diagram without switching to its tab (background refresh). */
  setErd: (e: { source: string; title?: string } | null, opts?: { focus?: boolean }) => void;
  erdFocus: number;
  editorSql: { sql: string; nonce: number } | null;
  openInEditor: (sql: string) => void;
  composer: { text: string; nonce: number } | null;
  setComposer: (text: string) => void;
}

export const useApp = create<AppState>((set) => ({
  me: null,
  setMe: (me) =>
    set({
      me,
      connectionId: me?.preferences?.default_connection ?? me?.connections?.[0]?.id ?? null,
    }),
  page: 'workspace',
  setPage: (page) => set({ page }),
  connectionId: null,
  setConnectionId: (connectionId) => set({ connectionId }),
  sessionId: null,
  setSessionId: (sessionId) => set({ sessionId }),
  result: null,
  setResult: (result) => set({ result }),
  resultBusy: false,
  setResultBusy: (resultBusy) => set({ resultBusy }),
  erd: null,
  erdFocus: 0,
  setErd: (erd, opts) =>
    set((s) => ({ erd, erdFocus: opts?.focus === false ? s.erdFocus : s.erdFocus + 1 })),
  editorSql: null,
  openInEditor: (sql) => set({ editorSql: { sql, nonce: Date.now() } }),
  composer: null,
  setComposer: (text) => set({ composer: { text, nonce: Date.now() } }),
}));
