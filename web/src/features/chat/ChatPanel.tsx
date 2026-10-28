import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, del, get, patch, post, postSSE } from '../../api/client';
import { useApp } from '../../app/store';
import { useExec } from '../../lib/exec';
import { Icon, IconButton } from '../../lib/icons';
import { speechSupported, useSpeech } from '../../lib/speech';
import { copyText, toastError } from '../../lib/toast';
import { Mascot } from '../../mascot/Mascot';
import { useMood } from '../../mascot/moodStore';
import { THEMES, pickGreeting } from '../../theme/themes';
import { blocksToMarkdown, blocksToSpeech, BlockView, type Block } from './Blocks';

interface Msg {
  id: string;
  role: 'USER' | 'ASSISTANT' | 'SYSTEM_EVENT';
  content: Block[];
  artifacts?: Record<string, any>;
  streaming?: boolean;
  feedback?: number | null;
}

const p = (text: string): Block => ({ type: 'paragraph', content: [{ type: 'text', text }] });

export function ChatPanel() {
  const me = useApp((s) => s.me)!;
  const theme = me.theme;
  const T = THEMES[theme];
  const connectionId = useApp((s) => s.connectionId);
  const setConnectionId = useApp((s) => s.setConnectionId);
  const sessionId = useApp((s) => s.sessionId);
  const setSessionId = useApp((s) => s.setSessionId);
  const composer = useApp((s) => s.composer);
  const mood = useMood((s) => s.mood);
  const setMood = useMood((s) => s.setMood);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [greeting] = useState(() => pickGreeting());
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [starters, setStarters] = useState<any[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [search, setSearch] = useState('');
  const [refs, setRefs] = useState<any[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const createdHere = useRef<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastAiRun = useExec((s) => s.lastAiRun);
  const conn = me.connections.find((c) => c.id === connectionId);

  useEffect(() => {
    get('/starter-prompts')
      .then((r) => setStarters(r.items))
      .catch(() => undefined);
    get('/rag/documents')
      .then((r) => setRefs(r.items.filter((d: any) => d.status === 'READY')))
      .catch(() => undefined);
  }, []);
  const refreshSessions = useCallback((q = '') => {
    get(`/chat/sessions${q ? `?q=${encodeURIComponent(q)}` : ''}`)
      .then((r) => setSessions(r.items))
      .catch(() => undefined);
  }, []);
  useEffect(() => refreshSessions(), [refreshSessions]);

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    if (createdHere.current === sessionId) return; // just created by send(): keep the in-flight messages
    get(`/chat/sessions/${sessionId}`)
      .then((s) => {
        setMessages(s.messages.map((m: any) => ({ ...m, content: m.content })));
        if (s.connection_id) setConnectionId(s.connection_id);
      })
      .catch(() => setSessionId(null));
  }, [sessionId, setConnectionId, setSessionId]);

  // stop reading aloud when switching conversations or leaving the chat
  useEffect(() => () => useSpeech.getState().stop(), [sessionId]);

  useEffect(() => {
    if (composer) {
      setText(composer.text);
      inputRef.current?.focus();
    }
  }, [composer]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  async function ensureSession(): Promise<string | null> {
    if (sessionId) return sessionId;
    try {
      const s = await post('/chat/sessions', { connection_id: connectionId });
      createdHere.current = s.id;
      setSessionId(s.id);
      return s.id;
    } catch (e) {
      toastError((e as ApiError).message);
      return null;
    }
  }

  const send = useCallback(
    async (prompt: string, intent?: string, runId?: string) => {
      if (!prompt.trim() || busy) return;
      const sid = await ensureSession();
      if (!sid) return;
      setBusy(true);
      setText('');
      setMood('thinking');
      const tempUser: Msg = { id: `u-${Date.now()}`, role: 'USER', content: [p(prompt)] };
      const tempBot: Msg = {
        id: `a-${Date.now()}`,
        role: 'ASSISTANT',
        content: [p('')],
        streaming: true,
      };
      setMessages((m) => [...m, tempUser, tempBot]);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let botId = tempBot.id;
      try {
        await postSSE(
          `/chat/sessions/${sid}/messages`,
          { prompt, intent, run_id: runId },
          (ev, data) => {
            if (ev === 'message.started') {
              botId = data.id;
              setMessages((m) => m.map((x) => (x.id === tempBot.id ? { ...x, id: data.id } : x)));
            } else if (ev === 'token.delta') {
              setMessages((m) =>
                m.map((x) => {
                  if (x.id !== botId) return x;
                  const first = x.content[0];
                  const txt = (first?.content?.[0]?.text || '') + data.text;
                  return { ...x, content: [p(txt), ...x.content.slice(1)] };
                }),
              );
            } else if (['sql.proposed', 'mermaid', 'insight', 'block'].includes(ev)) {
              setMessages((m) =>
                m.map((x) => (x.id === botId ? { ...x, content: [...x.content, data] } : x)),
              );
              if (ev === 'mermaid')
                useApp.getState().setErd({ source: data.props.source, title: data.props.title });
            } else if (ev === 'mood') {
              setMood(data.mood);
            } else if (ev === 'message.completed') {
              setMessages((m) =>
                m.map((x) =>
                  x.id === botId
                    ? {
                        id: data.id,
                        role: 'ASSISTANT',
                        content: data.content,
                        artifacts: data.artifacts,
                      }
                    : x,
                ),
              );
              if (data.artifacts?.mermaid)
                useApp
                  .getState()
                  .setErd({ source: data.artifacts.mermaid, title: 'Entity relationships' });
            } else if (ev === 'error') {
              setMessages((m) =>
                m.map((x) =>
                  x.id === botId
                    ? {
                        ...x,
                        streaming: false,
                        content: [p(data.title)],
                        artifacts: { error: true },
                      }
                    : x,
                ),
              );
            }
          },
          ctrl.signal,
        );
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          setMood('confused');
          const msg =
            e instanceof ApiError ? e.message : 'The connection dropped while I was thinking.';
          setMessages((m) =>
            m.map((x) =>
              x.id === botId
                ? { ...x, streaming: false, content: [p(msg)], artifacts: { error: true } }
                : x,
            ),
          );
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
        refreshSessions(search);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [busy, sessionId, connectionId, search],
  );

  // After an AI query runs, ask for an interpretation automatically (TS §3.3 flow).
  const lastHandled = useRef<number>(0);
  useEffect(() => {
    if (lastAiRun && lastAiRun.nonce !== lastHandled.current && sessionId) {
      lastHandled.current = lastAiRun.nonce;
      send('Interpret these results.', 'INTERPRET_RESULT', lastAiRun.run_id);
    }
  }, [lastAiRun, sessionId, send]);

  async function switchConnection(id: string) {
    setConnectionId(id);
    if (sessionId) {
      await patch(`/chat/sessions/${sessionId}`, { connection_id: id }).catch(() => undefined);
      const s = await get(`/chat/sessions/${sessionId}`);
      setMessages(s.messages);
    }
  }

  async function feedback(m: Msg, v: number) {
    await post(`/chat/messages/${m.id}/feedback`, { value: v }).catch(() => undefined);
    setMessages((ms) => ms.map((x) => (x.id === m.id ? { ...x, feedback: v } : x)));
  }

  const empty = messages.length === 0;
  return (
    <div className="chat" data-mood={mood}>
      <div className="chat-head">
        <h2 className="panel-title">
          Chat with {T.brand}
          <svg
            className="title-paw"
            viewBox="-14 -16 28 28"
            width="20"
            height="20"
            aria-hidden="true"
          >
            <ellipse cx="0" cy="5" rx="7" ry="6" fill="currentColor" />
            <ellipse cx="-7" cy="-4" rx="2.8" ry="3.6" fill="currentColor" />
            <ellipse cx="-2.4" cy="-8" rx="2.8" ry="3.6" fill="currentColor" />
            <ellipse cx="3" cy="-8" rx="2.8" ry="3.6" fill="currentColor" />
            <ellipse cx="7.6" cy="-4" rx="2.8" ry="3.6" fill="currentColor" />
          </svg>
        </h2>
        <span className="grow" />
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="conn-badge" aria-label="Choose database connection">
              {conn ? (
                <>
                  <span className="conn-dot" /> Connected: <strong>{conn.name}</strong>
                  <span className="muted">
                    ({conn.engine.toLowerCase()}, {conn.country_code})
                  </span>
                </>
              ) : (
                'Choose a database'
              )}
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content className="dd-content" align="end" sideOffset={6}>
              <DropdownMenu.Label className="dd-label">Databases you can query</DropdownMenu.Label>
              {me.connections.length === 0 && (
                <DropdownMenu.Item className="dd-item" disabled>
                  No databases granted yet. Ask an admin.
                </DropdownMenu.Item>
              )}
              {me.connections.map((c) => (
                <DropdownMenu.Item
                  key={c.id}
                  className="dd-item"
                  onSelect={() => switchConnection(c.id)}
                >
                  {c.id === connectionId ? '✓ ' : ''}
                  {c.name}{' '}
                  <span className="muted">
                    ({c.engine.toLowerCase()}, {c.country_code})
                  </span>
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
        <DropdownMenu.Root onOpenChange={(o) => o && refreshSessions(search)}>
          <DropdownMenu.Trigger asChild>
            <IconButton
              label="Chat history"
              icon={<Icon.history />}
              className="ibtn-tip-end ibtn-boxed"
            />
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content className="dd-content sessions-dd" align="end" sideOffset={6}>
              <div className="sessions-search" onKeyDown={(e) => e.stopPropagation()}>
                <input
                  className="input"
                  placeholder="Search chats"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    refreshSessions(e.target.value);
                  }}
                />
              </div>
              <DropdownMenu.Item className="dd-item" onSelect={() => setSessionId(null)}>
                ＋ New chat
              </DropdownMenu.Item>
              <DropdownMenu.Separator className="dd-sep" />
              {sessions.length === 0 && <div className="dd-label">No chats yet.</div>}
              {sessions.map((s) => (
                <div key={s.id} className="session-row">
                  <DropdownMenu.Item className="dd-item grow" onSelect={() => setSessionId(s.id)}>
                    {s.id === sessionId ? '● ' : ''}
                    {s.title}
                  </DropdownMenu.Item>
                  <button
                    className="btn btn-sm btn-ghost"
                    aria-label={`Rename ${s.title}`}
                    onClick={async () => {
                      const t = prompt('Rename chat', s.title);
                      if (t) {
                        await patch(`/chat/sessions/${s.id}`, { title: t });
                        refreshSessions(search);
                      }
                    }}
                  >
                    ✎
                  </button>
                  <button
                    className="btn btn-sm btn-ghost"
                    aria-label={`Delete ${s.title}`}
                    onClick={async () => {
                      await del(`/chat/sessions/${s.id}`);
                      if (s.id === sessionId) setSessionId(null);
                      refreshSessions(search);
                    }}
                  >
                    🗑
                  </button>
                </div>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>

      <div className="chat-log" ref={logRef}>
        {empty && (
          <div className="chat-welcome">
            {me.ui.mascot_enabled && <Mascot theme={theme} mood={mood} size={168} />}
            <div className="bubble bot">
              <p>{greeting}</p>
              {!conn && <p className="muted">Pick a database at the top right to get started.</p>}
            </div>
            {starters.length > 0 && (
              <div className="starters starters-welcome" aria-label="Starter prompts">
                {starters.map((s) => (
                  <button
                    type="button"
                    key={s.id}
                    className="chip"
                    title={s.prompt}
                    onClick={() => {
                      if (s.intent === 'REVIEW_SQL') {
                        setText(s.prompt + '\n```sql\n\n```');
                        inputRef.current?.focus();
                      } else
                        send(
                          s.prompt,
                          s.intent,
                          s.intent === 'INTERPRET_RESULT'
                            ? useApp.getState().result?.run_id
                            : undefined,
                        );
                    }}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((m) =>
          m.role === 'SYSTEM_EVENT' ? (
            <div key={m.id} className="sys-event">
              {(m.content[0]?.content || []).map((c) => c.text).join('')}
            </div>
          ) : m.role === 'USER' ? (
            <div key={m.id} className="msg user">
              <div className="bubble user">
                {(m.content[0]?.content || []).map((c) => c.text).join('')}
              </div>
            </div>
          ) : (
            <div key={m.id} className={`msg bot ${m.artifacts?.error ? 'is-error' : ''}`}>
              <div className="msg-avatar">
                <Mascot
                  theme={theme}
                  headOnly
                  size={40}
                  mood={m.streaming ? 'thinking' : 'idle'}
                  title=""
                />
              </div>
              <div className="msg-body">
                <div className="bubble bot">
                  {m.streaming && !m.content[0]?.content?.[0]?.text ? (
                    <span className="typing" aria-label="Thinking">
                      <i />
                      <i />
                      <i />
                    </span>
                  ) : (
                    <BlockView b={m.content[0]} onAsk={(t) => send(t)} />
                  )}
                </div>
                {m.content.slice(1).map((b, i) => (
                  <BlockView
                    key={i}
                    b={b}
                    messageId={m.id}
                    sessionId={sessionId}
                    onAsk={(t) => send(t)}
                  />
                ))}
                {!m.streaming && (
                  <div className="msg-tools">
                    <IconButton
                      label="Copy reply"
                      icon={<Icon.copy />}
                      onClick={() => copyText(blocksToMarkdown(m.content), 'Message copied')}
                    />
                    {speechSupported && (
                      <ReadAloud id={m.id} text={() => blocksToSpeech(m.content)} />
                    )}
                    <IconButton
                      label="Helpful"
                      icon={<Icon.thumbUp />}
                      className={m.feedback === 1 ? 'on' : ''}
                      aria-pressed={m.feedback === 1}
                      onClick={() => feedback(m, 1)}
                    />
                    <IconButton
                      label="Not helpful"
                      icon={<Icon.thumbDown />}
                      className={m.feedback === -1 ? 'on' : ''}
                      aria-pressed={m.feedback === -1}
                      onClick={() => feedback(m, -1)}
                    />
                  </div>
                )}
              </div>
            </div>
          ),
        )}
      </div>

      <div className="composer-wrap">
        {!empty && me.ui.mascot_enabled && (
          <div className="chat-mascot" aria-live="polite">
            <Mascot theme={theme} mood={mood} size={96} />
          </div>
        )}
        {refs.length > 0 && (
          <div className="refs">
            <span className="refs-label">Reference:</span>
            {refs.slice(0, 4).map((r) => (
              <span key={r.id} className="ref-chip" title={`${r.scope.toLowerCase()} reference`}>
                <span className={`ref-ico ext-${r.filename.split('.').pop()}`} />
                {r.filename}
              </span>
            ))}
            {refs.length > 4 && <span className="muted">+{refs.length - 4} more</span>}
          </div>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <textarea
            ref={inputRef}
            className="composer-input"
            placeholder={`Ask ${T.brand} anything…`}
            value={text}
            rows={2}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send(text);
              }
            }}
            aria-label="Message"
          />
          <div className="composer-row">
            <span className="composer-hint muted small">
              Enter to send · Shift+Enter for a new line
            </span>
            {busy ? (
              <button
                type="button"
                className="submit stop"
                onClick={() => abortRef.current?.abort()}
              >
                Stop
              </button>
            ) : (
              <button
                className={`submit submit-${theme}`}
                disabled={!text.trim()}
                aria-label="Send"
              >
                {theme === 'morning' ? (
                  <svg viewBox="-14 -16 28 28" width="26" height="26" aria-hidden="true">
                    <ellipse cx="0" cy="5" rx="7" ry="6" fill="currentColor" />
                    <ellipse cx="-7" cy="-4" rx="2.8" ry="3.6" fill="currentColor" />
                    <ellipse cx="-2.4" cy="-8" rx="2.8" ry="3.6" fill="currentColor" />
                    <ellipse cx="3" cy="-8" rx="2.8" ry="3.6" fill="currentColor" />
                    <ellipse cx="7.6" cy="-4" rx="2.8" ry="3.6" fill="currentColor" />
                  </svg>
                ) : (
                  'Submit'
                )}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

/** Read a reply aloud (browser speech); the same button stops it. Only one reply speaks at a time. */
function ReadAloud({ id, text }: { id: string; text: () => string }) {
  const speaking = useSpeech((s) => s.speakingId === id);
  return (
    <IconButton
      label={speaking ? 'Stop reading' : 'Read aloud'}
      icon={speaking ? <Icon.stop /> : <Icon.speaker />}
      className={speaking ? 'on' : ''}
      aria-pressed={speaking}
      onClick={() =>
        speaking ? useSpeech.getState().stop() : useSpeech.getState().speak(id, text())
      }
    />
  );
}
