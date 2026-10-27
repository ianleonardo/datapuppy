// Thin fetch wrapper: session cookie + double-submit CSRF header, RFC 7807 problem+json errors (TS §20).

export class ApiError extends Error {
  code: string;
  status: number;
  body: Record<string, any>;
  constructor(status: number, body: Record<string, any>) {
    super(body?.title || `HTTP ${status}`);
    this.code = body?.code || `HTTP-${status}`;
    this.status = status;
    this.body = body || {};
  }
}

let csrf = '';
export function setCsrf(token: string) {
  csrf = token;
}
function csrfToken(): string {
  if (csrf) return csrf;
  const m = document.cookie.match(/(?:^|;\s*)sqlassist_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

type Listener = (e: ApiError) => void;
const authListeners = new Set<Listener>();
export function onAuthError(fn: Listener) {
  authListeners.add(fn);
  return () => authListeners.delete(fn);
}

export async function api<T = any>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') headers.set('X-CSRF-Token', csrfToken());
  let body = init.body;
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    method,
    headers,
    body,
    credentials: 'same-origin',
  });
  if (!res.ok) {
    let data: any = {};
    try {
      data = await res.json();
    } catch {
      data = { title: res.statusText };
    }
    const err = new ApiError(res.status, data);
    if (res.status === 401 && err.code === 'E-AUTH-001') authListeners.forEach((l) => l(err));
    throw err;
  }
  const ct = res.headers.get('content-type') || '';
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>;
}

export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, json?: unknown) =>
  api<T>(p, { method: 'POST', json: json ?? {} });
export const put = <T = any>(p: string, json?: unknown) =>
  api<T>(p, { method: 'PUT', json: json ?? {} });
export const patch = <T = any>(p: string, json?: unknown) =>
  api<T>(p, { method: 'PATCH', json: json ?? {} });
export const del = <T = any>(p: string) => api<T>(p, { method: 'DELETE' });

/** POST that returns an SSE stream (chat). Calls onEvent for each `event:`/`data:` frame. */
export async function postSSE(
  path: string,
  json: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/v1${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken(),
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(json),
    credentials: 'same-origin',
    signal,
  });
  if (!res.ok || !res.body) {
    let data: any = {};
    try {
      data = await res.json();
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, data);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let ev = 'message';
      const data: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) ev = line.slice(7);
        else if (line.startsWith('data: ')) data.push(line.slice(6));
      }
      if (data.length) {
        try {
          onEvent(ev, JSON.parse(data.join('\n')));
        } catch {
          onEvent(ev, data.join('\n'));
        }
      }
    }
  }
}

/** Follow a background job via SSE until it finishes. */
export function watchJob(jobId: string, onUpdate: (j: any) => void): () => void {
  const es = new EventSource(`/api/v1/jobs/${jobId}/events`, { withCredentials: true });
  const handler = (e: MessageEvent) => {
    try {
      const d = JSON.parse(e.data);
      onUpdate(d);
      const st = d.status;
      if (st === 'SUCCEEDED' || st === 'FAILED' || st === 'CANCELLED') es.close();
    } catch {
      /* ignore */
    }
  };
  es.addEventListener('status', handler as any);
  es.addEventListener('progress', handler as any);
  es.onerror = () => es.close();
  return () => es.close();
}

export async function waitJob(
  jobId: string,
  onProgress?: (p: any) => void,
  timeoutMs = 600_000,
): Promise<any> {
  const t0 = Date.now();
  for (;;) {
    const j = await get(`/jobs/${jobId}`);
    onProgress?.(j);
    if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(j.status)) return j;
    if (Date.now() - t0 > timeoutMs) throw new Error('job timeout');
    await new Promise((r) => setTimeout(r, 600));
  }
}
