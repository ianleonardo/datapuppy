import { Select } from '../../lib/Select';
import { useEffect, useState } from 'react';
import { del, post, put } from '../../api/client';
import { toast } from '../../lib/toast';
import { act, Card, Field, Modal, Section, Toggle, useLoad } from './kit';

/** LLM providers (TS §11, §18.4): URLs, models, API key (write-only), max model length, TLS/proxy bypass, test, primary. */
export function LlmSection() {
  const p = useLoad<any>('/admin/llm-providers');
  const [edit, setEdit] = useState<any>(null);
  const [tests, setTests] = useState<Record<string, any>>({});

  async function test(id: string) {
    setTests((t) => ({ ...t, [id]: { busy: true } }));
    const r = await act(() => post(`/admin/llm-providers/${id}/test`));
    setTests((t) => ({ ...t, [id]: r }));
    p.reload();
  }
  async function save() {
    const body = { ...edit };
    if (body.api_key === undefined || body.api_key === null) delete body.api_key;
    try {
      body.params =
        typeof body.paramsText === 'string' ? JSON.parse(body.paramsText || '{}') : body.params;
    } catch {
      return toast('Extra parameters must be valid JSON');
    }
    delete body.paramsText;
    const r = await act(() =>
      edit.id ? put(`/admin/llm-providers/${edit.id}`, body) : post('/admin/llm-providers', body),
    );
    if (r) {
      toast('Provider saved. It takes effect on the next request.');
      setEdit(null);
      p.reload();
    }
  }
  const primary = async (id: string, f: 'chat' | 'embed') => {
    if (
      f === 'embed' &&
      !confirm(
        'Switching the embedding provider re-embeds every document and schema card. Continue?',
      )
    )
      return;
    await act(
      () => post(`/admin/llm-providers/${id}/set-primary?for=${f}`),
      (r: any) =>
        toast(
          r.reindex_job
            ? 'Embedding provider switched; re-index started'
            : 'Primary chat provider set',
        ),
    );
    p.reload();
  };

  return (
    <Section
      title="LLM providers"
      intro="Spirare (internal, OpenAI-compatible) and Google Gemini. Embeddings never fail over, because vectors from different models can't be compared."
      actions={
        <button
          className="btn btn-primary"
          onClick={() =>
            setEdit({
              kind: 'SPIRARE',
              display_name: '',
              chat_url: '',
              chat_model: '',
              embed_url: '',
              embed_model: '',
              max_model_len: 32768,
              max_output_tokens: 4096,
              verify_ssl: true,
              use_proxy: true,
              proxy_url: '',
              ca_bundle_path: '',
              timeout_s: 120,
              paramsText: '{\n  "temperature": 0.1,\n  "structured_output_mode": "json_schema"\n}',
              is_enabled: true,
            })
          }
        >
          Add provider
        </button>
      }
    >
      <div className="conn-cards">
        {p.data?.items.map((x: any) => {
          const t = tests[x.id] || x.last_test;
          return (
            <Card key={x.id} className="conn-card">
              <div className="cc-head">
                <strong>{x.display_name}</strong>
                <span className="pill pill-info">{x.kind.toLowerCase()}</span>
                {x.is_primary_chat && <span className="pill pill-ok">primary chat</span>}
                {x.is_primary_embed && <span className="pill pill-ok">primary embeddings</span>}
                {!x.is_enabled && <span className="pill pill-warn">disabled</span>}
              </div>
              <dl className="kv small">
                <dt>Chat</dt>
                <dd className="mono">{x.chat_model}</dd>
                <dt>Embeddings</dt>
                <dd className="mono">
                  {x.embed_model || '—'} {x.embed_dim ? `(${x.embed_dim}-d)` : ''}
                </dd>
                <dt>Max model length</dt>
                <dd>{x.max_model_len?.toLocaleString()} tokens</dd>
                <dt>API key</dt>
                <dd>{x.api_key_set ? 'saved (hidden)' : 'not set'}</dd>
              </dl>
              {x.warnings.map((w: string) => (
                <p key={w} className="warn-note small">
                  {w}
                </p>
              ))}
              {t && !t.busy && (
                <p className="small">
                  Chat: {t.chat?.ok ? `ok, ${t.chat.latency_ms} ms` : t.chat?.error || '—'}
                  <br />
                  Embeddings:{' '}
                  {t.embed?.ok ? `ok, ${t.embed_dim} dimensions` : t.embed?.error || '—'}
                  {t.hint && <span className="muted"> {t.hint}</span>}
                </p>
              )}
              <div className="row-actions">
                <button
                  className="btn btn-sm"
                  onClick={() => test(x.id)}
                  disabled={tests[x.id]?.busy}
                >
                  {tests[x.id]?.busy ? 'Testing…' : 'Test'}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    setEdit({
                      ...x,
                      api_key: undefined,
                      paramsText: JSON.stringify(x.params, null, 2),
                    })
                  }
                >
                  Edit
                </button>
                {!x.is_primary_chat && (
                  <button className="btn btn-sm btn-ghost" onClick={() => primary(x.id, 'chat')}>
                    Use for chat
                  </button>
                )}
                {!x.is_primary_embed && (
                  <button className="btn btn-sm btn-ghost" onClick={() => primary(x.id, 'embed')}>
                    Use for embeddings
                  </button>
                )}
              </div>
            </Card>
          );
        })}
      </div>
      {edit && (
        <Modal
          title={edit.id ? `Edit ${edit.display_name}` : 'Add provider'}
          onClose={() => setEdit(null)}
          wide
        >
          <div className="adm-form">
            <Field label="Kind">
              <Select
                className="select"
                value={edit.kind}
                onChange={(e) => setEdit({ ...edit, kind: e.target.value })}
                disabled={!!edit.id}
              >
                <option value="SPIRARE">Spirare (OpenAI-compatible)</option>
                <option value="GEMINI">Google Gemini</option>
              </Select>
            </Field>
            <Field label="Display name">
              <input
                className="input"
                value={edit.display_name}
                onChange={(e) => setEdit({ ...edit, display_name: e.target.value })}
              />
            </Field>
            <Field
              label="Chat URL"
              wide
              hint={edit.kind === 'GEMINI' ? '{model} is replaced with the chat model' : undefined}
            >
              <input
                className="input"
                value={edit.chat_url}
                onChange={(e) => setEdit({ ...edit, chat_url: e.target.value })}
              />
            </Field>
            <Field label="Chat model">
              <input
                className="input"
                value={edit.chat_model}
                onChange={(e) => setEdit({ ...edit, chat_model: e.target.value })}
              />
            </Field>
            <Field label="Embedding URL" wide>
              <input
                className="input"
                value={edit.embed_url || ''}
                onChange={(e) => setEdit({ ...edit, embed_url: e.target.value })}
              />
            </Field>
            <Field label="Embedding model">
              <input
                className="input"
                value={edit.embed_model || ''}
                onChange={(e) => setEdit({ ...edit, embed_model: e.target.value })}
              />
            </Field>
            <Field
              label="API key"
              hint={
                edit.api_key_set
                  ? 'Saved. Leave blank to keep it, or type a new one.'
                  : 'Stored encrypted; never shown again.'
              }
            >
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={edit.api_key ?? ''}
                onChange={(e) => setEdit({ ...edit, api_key: e.target.value || undefined })}
              />
            </Field>
            <Field label="Max model length (tokens)" hint="Must match the served context window">
              <input
                className="input"
                type="number"
                value={edit.max_model_len ?? ''}
                onChange={(e) => setEdit({ ...edit, max_model_len: Number(e.target.value) })}
              />
            </Field>
            <Field label="Max output tokens">
              <input
                className="input"
                type="number"
                value={edit.max_output_tokens ?? ''}
                onChange={(e) => setEdit({ ...edit, max_output_tokens: Number(e.target.value) })}
              />
            </Field>
            <Field label="Timeout (seconds)">
              <input
                className="input"
                type="number"
                value={edit.timeout_s}
                onChange={(e) => setEdit({ ...edit, timeout_s: Number(e.target.value) })}
              />
            </Field>
            <Toggle
              checked={edit.verify_ssl}
              onChange={(v) => setEdit({ ...edit, verify_ssl: v })}
              label="Verify TLS certificate"
            />
            <Toggle
              checked={edit.use_proxy}
              onChange={(v) => setEdit({ ...edit, use_proxy: v })}
              label="Use proxy (off bypasses every proxy, including environment variables)"
            />
            {!edit.verify_ssl && (
              <p className="warn-note">
                TLS verification is off for this provider. Traffic could be intercepted on the
                network. Prefer a CA bundle below.
              </p>
            )}
            <Field
              label="CA bundle path"
              hint="Internal CA certificate file, used when verification is on"
            >
              <input
                className="input"
                value={edit.ca_bundle_path || ''}
                onChange={(e) => setEdit({ ...edit, ca_bundle_path: e.target.value })}
              />
            </Field>
            <Field label="Proxy URL" hint="Only used when the proxy is on">
              <input
                className="input"
                value={edit.proxy_url || ''}
                onChange={(e) => setEdit({ ...edit, proxy_url: e.target.value })}
              />
            </Field>
            <Field
              label="Extra parameters (JSON)"
              wide
              hint="temperature, top_p, structured_output_mode (json_schema, json_object, prompt_only), auth_header, embed_batch, tokenizer_path, output_dimensionality"
            >
              <textarea
                className="textarea mono"
                rows={6}
                value={edit.paramsText}
                onChange={(e) => setEdit({ ...edit, paramsText: e.target.value })}
              />
            </Field>
            <Toggle
              checked={edit.is_enabled}
              onChange={(v) => setEdit({ ...edit, is_enabled: v })}
              label="Enabled"
            />
          </div>
          <div className="modal-actions">
            {edit.id && (
              <button
                className="btn btn-danger"
                onClick={() =>
                  confirm('Delete this provider?') &&
                  act(
                    () => del(`/admin/llm-providers/${edit.id}`),
                    () => {
                      setEdit(null);
                      p.reload();
                    },
                  )
                }
              >
                Delete
              </button>
            )}
            <span className="grow" />
            <button className="btn" onClick={() => setEdit(null)}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={save}>
              Save provider
            </button>
          </div>
        </Modal>
      )}
    </Section>
  );
}

const SETTING_LABELS: Record<string, [string, string?]> = {
  'safeguard.max_rows': ['Rows before confirmation'],
  'safeguard.max_cols': ['Columns before confirmation'],
  'safeguard.count_timeout_s': ['COUNT(*) timeout (s)', 'Falls back to the optimizer estimate'],
  'safeguard.statement_timeout_s': ['Statement timeout (s)'],
  'grid.page_size_default': ['Default page size'],
  'results.handle_ttl_min': ['Result expiry (minutes)'],
  'export.sync_row_limit': [
    'Excel rows exported immediately',
    'Larger exports run in the background',
  ],
  'export.file_ttl_h': ['Keep exports for (hours)'],
  'rag.chunk_tokens': ['Chunk size (tokens)'],
  'rag.chunk_overlap': ['Chunk overlap (tokens)'],
  'rag.knowledge_top_k': ['Reference passages per question'],
  'rag.schema_max_tables': ['Max tables in AI context'],
  'ai.max_repair_rounds': ['SQL repair attempts'],
  'ai.history_turns': ['Conversation turns remembered'],
  'ai.prompt_budget_cap': ['Prompt budget cap (tokens)'],
  'ai.interpret_max_rows': ['Rows profiled for insights'],
  'ai.interpret_sample_rows': ['Sample rows sent for insights'],
  'ai.failover_enabled': ['Chat failover to the other provider'],
  'ui.mascot_enabled': ['Show the mascot'],
  'logging.level': ['Log level', 'DEBUG, INFO, WARNING or ERROR'],
  'duckdb.backup_cron': ['Nightly backup schedule (cron)'],
  'duckdb.backup_keep': ['Backups to keep'],
  'retention.audit_days': ['Auto-purge audit after (days)', '0 = off'],
  'retention.chat_days': ['Auto-purge chats after (days)', '0 = off'],
  'audit.purge_mirror_files': ['Purged audit files', 'delete or move'],
  'metrics.enabled': ['Prometheus /metrics endpoint'],
};

export function SettingsSection() {
  const s = useLoad<any>('/admin/settings');
  const [vals, setVals] = useState<Record<string, any>>({});
  useEffect(() => {
    if (s.data) setVals(s.data.values);
  }, [s.data]);
  if (!s.data) return <Section title="Settings">Loading…</Section>;
  const save = async (k: string) => {
    await act(
      () => put(`/admin/settings/${k}`, { value: vals[k] }),
      () => toast('Setting saved'),
    );
  };
  return (
    <Section
      title="Settings"
      intro="Runtime settings. Changes apply immediately without a restart."
    >
      <Card>
        <div className="settings-grid">
          {Object.entries(SETTING_LABELS).map(([k, [label, hint]]) => {
            const v = vals[k];
            const isBool = typeof v === 'boolean';
            return (
              <div key={k} className="setting-row">
                <div>
                  <div className="adm-label">{label}</div>
                  {hint && <div className="adm-hint">{hint}</div>}
                </div>
                {isBool ? (
                  <Toggle
                    checked={v}
                    onChange={(x) => {
                      setVals({ ...vals, [k]: x });
                      put(`/admin/settings/${k}`, { value: x }).then(() => toast('Setting saved'));
                    }}
                    label={v ? 'On' : 'Off'}
                  />
                ) : (
                  <input
                    className="input"
                    value={v ?? ''}
                    onChange={(e) =>
                      setVals({
                        ...vals,
                        [k]:
                          typeof s.data.defaults[k] === 'number'
                            ? Number(e.target.value)
                            : e.target.value,
                      })
                    }
                    onBlur={() => v !== s.data.values[k] && save(k)}
                  />
                )}
              </div>
            );
          })}
        </div>
      </Card>
      <Card>
        <h4>Assistant persona</h4>
        <p className="small muted">
          {'{mascot}'} becomes QueryPup, Tiangou or Barky depending on the theme. Error codes and
          audit text never use this voice.
        </p>
        <textarea
          className="textarea"
          rows={4}
          style={{ width: '100%' }}
          value={vals['ai.persona'] || ''}
          onChange={(e) => setVals({ ...vals, 'ai.persona': e.target.value })}
        />
        <div className="modal-actions">
          <button className="btn btn-primary" onClick={() => save('ai.persona')}>
            Save persona
          </button>
        </div>
      </Card>
      <Card>
        <h4>Theme windows (local time)</h4>
        <div className="adm-inline">
          {(['morning', 'afternoon', 'night'] as const).map((t) => (
            <span key={t} className="adm-inline">
              <strong>{t}</strong>
              <input
                className="input input-sm"
                style={{ width: 50 }}
                type="number"
                value={vals['theme.windows']?.[t]?.[0] ?? ''}
                onChange={(e) =>
                  setVals({
                    ...vals,
                    'theme.windows': {
                      ...vals['theme.windows'],
                      [t]: [Number(e.target.value), vals['theme.windows'][t][1]],
                    },
                  })
                }
              />
              to
              <input
                className="input input-sm"
                style={{ width: 50 }}
                type="number"
                value={vals['theme.windows']?.[t]?.[1] ?? ''}
                onChange={(e) =>
                  setVals({
                    ...vals,
                    'theme.windows': {
                      ...vals['theme.windows'],
                      [t]: [vals['theme.windows'][t][0], Number(e.target.value)],
                    },
                  })
                }
              />
            </span>
          ))}
          <button className="btn" onClick={() => save('theme.windows')}>
            Save windows
          </button>
        </div>
      </Card>
    </Section>
  );
}

export function StartersSection() {
  const s = useLoad<any>('/admin/starter-prompts');
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    if (s.data) setItems(s.data.items);
  }, [s.data]);
  const move = (i: number, d: number) => {
    const next = [...items];
    const [x] = next.splice(i, 1);
    next.splice(i + d, 0, x);
    setItems(next);
  };
  return (
    <Section
      title="Starter prompts"
      intro="The chips shown above the chat box. Order matters."
      actions={
        <button
          className="btn btn-primary"
          onClick={() =>
            act(
              () => put('/admin/starter-prompts', { items }),
              () => toast('Starter prompts saved'),
            )
          }
        >
          Save prompts
        </button>
      }
    >
      {items.map((it, i) => (
        <Card key={i} className="starter-card">
          <div className="adm-inline">
            <input
              className="input"
              value={it.label}
              onChange={(e) =>
                setItems(items.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
              }
            />
            <Select
              className="select"
              value={it.intent}
              onChange={(e) =>
                setItems(items.map((x, j) => (j === i ? { ...x, intent: e.target.value } : x)))
              }
            >
              {[
                'GENERATE_SQL',
                'EXPLAIN_SCHEMA',
                'HYGIENE_CHECK',
                'REVIEW_SQL',
                'INTERPRET_RESULT',
                'CHITCHAT',
              ].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </Select>
            <Toggle
              checked={it.is_active}
              onChange={(v) =>
                setItems(items.map((x, j) => (j === i ? { ...x, is_active: v } : x)))
              }
              label="Shown"
            />
            <span className="grow" />
            <button
              className="icon-btn"
              disabled={i === 0}
              onClick={() => move(i, -1)}
              aria-label="Move up"
            >
              ↑
            </button>
            <button
              className="icon-btn"
              disabled={i === items.length - 1}
              onClick={() => move(i, 1)}
              aria-label="Move down"
            >
              ↓
            </button>
            <button
              className="icon-btn"
              onClick={() => setItems(items.filter((_, j) => j !== i))}
              aria-label="Remove"
            >
              ✕
            </button>
          </div>
          <textarea
            className="textarea"
            rows={2}
            style={{ width: '100%', marginTop: 6 }}
            value={it.prompt}
            onChange={(e) =>
              setItems(items.map((x, j) => (j === i ? { ...x, prompt: e.target.value } : x)))
            }
          />
        </Card>
      ))}
      <button
        className="btn"
        onClick={() =>
          setItems([
            ...items,
            { label: 'New prompt', prompt: '', intent: 'GENERATE_SQL', is_active: true },
          ])
        }
      >
        Add prompt
      </button>
    </Section>
  );
}
