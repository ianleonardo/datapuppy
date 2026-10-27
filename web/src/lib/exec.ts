import { create } from 'zustand';
import { ApiError, get, post } from '../api/client';
import { useApp } from '../app/store';
import { useMood } from '../mascot/moodStore';
import { toastError } from './toast';

/** Execute flow shared by chat cards and the SQL editor (TS §12): interception modal, grid update, moods. */
export interface Interception {
  rows: number | null;
  cols: number;
  method: string;
  thresholds: { max_rows: number; max_cols: number };
  resolve: (ok: boolean) => void;
}

interface ExecState {
  interception: Interception | null;
  set: (i: Interception | null) => void;
  lastAiRun: { run_id: string; message_id?: string; nonce: number } | null;
}
export const useExec = create<ExecState>((set) => ({
  interception: null,
  set: (interception) => set({ interception }),
  lastAiRun: null,
}));

export interface RunArgs {
  sql: string;
  origin: 'AI' | 'MANUAL' | 'REFINED';
  message_id?: string;
  session_id?: string | null;
  connection_id?: string | null;
}

export async function runQuery(a: RunArgs): Promise<any | null> {
  const app = useApp.getState();
  const conn = a.connection_id ?? app.connectionId;
  if (!conn) {
    toastError('Pick a database connection first.');
    return null;
  }
  const mood = useMood.getState();
  app.setResultBusy(true);
  mood.setMood('thinking');
  const body = {
    connection_id: conn,
    sql: a.sql,
    origin: a.origin,
    message_id: a.message_id,
    session_id: a.session_id ?? app.sessionId,
    confirmed_by_user: true,
    confirmed_large: false,
  };
  try {
    let res: any;
    try {
      res = await post('/queries/execute', body);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'E-EXE-020') {
        mood.setMood('protective', 0);
        const ok = await new Promise<boolean>((resolve) =>
          useExec.getState().set({
            rows: e.body.rows ?? null,
            cols: e.body.cols,
            method: e.body.row_count_method,
            thresholds: e.body.thresholds,
            resolve,
          }),
        );
        useExec.getState().set(null);
        if (!ok) {
          mood.setMood('idle');
          return null;
        }
        mood.setMood('thinking');
        res = await post('/queries/execute', {
          ...body,
          confirmed_large: true,
          run_id: e.body.run_id,
        });
      } else throw e;
    }
    useApp.getState().setResult({ ...res, sql: a.sql, connection_id: conn });
    mood.setMood('happy');
    if (a.origin !== 'MANUAL')
      useExec.setState({
        lastAiRun: { run_id: res.run_id, message_id: a.message_id, nonce: Date.now() },
      });
    return res;
  } catch (e) {
    const err = e as ApiError;
    mood.setMood(err.body?.destructive ? 'protective' : 'confused');
    toastError(err.message || 'The query failed.');
    return null;
  } finally {
    useApp.getState().setResultBusy(false);
  }
}

export async function loadPage(page: number, pageSize: number, sort?: string) {
  const app = useApp.getState();
  const r = app.result;
  if (!r) return;
  app.setResultBusy(true);
  try {
    const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (sort) q.set('sort', sort);
    const res = await get(`/results/${r.run_id}/rows?${q}`);
    app.setResult({ ...r, ...res });
  } catch (e) {
    const err = e as ApiError;
    if (err.code === 'E-EXE-040')
      toastError('This result expired. Click Re-run to fetch it again.');
    else toastError(err.message);
  } finally {
    app.setResultBusy(false);
  }
}
