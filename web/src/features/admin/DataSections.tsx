import { Select } from '../../lib/Select';
import { useState } from 'react';
import { api, del, post, put } from '../../api/client';
import { toast } from '../../lib/toast';
import { act, Card, Empty, Field, fmtDate, Modal, Section, Toggle, useLoad } from './kit';

export function CountriesSection() {
  const c = useLoad<any>('/admin/countries');
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  return (
    <Section
      title="Countries"
      intro="Countries group databases and drive country-wide access grants and references."
    >
      <Card>
        <div className="adm-inline">
          <input
            className="input"
            style={{ width: 70 }}
            placeholder="Code"
            maxLength={2}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
          <input
            className="input"
            placeholder="Country name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            className="btn btn-primary"
            disabled={code.length !== 2 || !name}
            onClick={() =>
              act(
                () => post('/admin/countries', { code, name }),
                () => {
                  setCode('');
                  setName('');
                  c.reload();
                },
              )
            }
          >
            Add country
          </button>
        </div>
      </Card>
      <table className="adm-table">
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Active</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {c.data?.items.map((x: any) => (
            <tr key={x.code}>
              <td className="mono">{x.code}</td>
              <td>{x.name}</td>
              <td>
                <Toggle
                  checked={x.is_active}
                  onChange={(v) =>
                    act(
                      () => post('/admin/countries', { ...x, is_active: v }),
                      () => c.reload(),
                    )
                  }
                  label={x.is_active ? 'Active' : 'Hidden'}
                />
              </td>
              <td className="row-actions">
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() =>
                    act(
                      () => del(`/admin/countries/${x.code}`),
                      () => c.reload(),
                    )
                  }
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

const ENGINE_FIELDS: Record<string, { k: string; label: string; hint?: string; type?: string }[]> =
  {
    DUCKDB: [
      {
        k: 'path',
        label: 'Database file path',
        hint: 'Absolute path on the server. Opened read-only. Must be outside the app data folder.',
      },
    ],
    POSTGRES: [
      { k: 'host', label: 'Host' },
      { k: 'port', label: 'Port', type: 'number' },
      { k: 'database', label: 'Database' },
      { k: 'search_path', label: 'Schema search path', hint: 'Optional, e.g. sales,public' },
      { k: 'sslmode', label: 'SSL mode', hint: 'disable, prefer, require, verify-full' },
    ],
    MARIADB: [
      { k: 'host', label: 'Host' },
      { k: 'port', label: 'Port', type: 'number' },
      { k: 'database', label: 'Database' },
    ],
    ORACLE: [
      { k: 'mode', label: 'Connect mode', hint: 'easy, tns or wallet' },
      { k: 'host', label: 'Host (easy)' },
      { k: 'port', label: 'Port (easy)', type: 'number' },
      { k: 'service_name', label: 'Service name (easy)' },
      { k: 'tns_alias', label: 'TNS alias (tns / wallet)' },
      { k: 'tns_admin', label: 'tnsnames.ora folder (tns)' },
    ],
  };
const ENGINE_DEFAULTS: Record<string, Record<string, any>> = {
  DUCKDB: { path: '' },
  POSTGRES: { host: '', port: 5432, database: '', sslmode: 'prefer' },
  MARIADB: { host: '', port: 3306, database: '' },
  ORACLE: { mode: 'easy', host: '', port: 1521, service_name: '' },
};

export function ConnectionsSection() {
  const conns = useLoad<any>('/admin/connections');
  const countries = useLoad<any>('/admin/countries');
  const [edit, setEdit] = useState<any>(null);
  const [tests, setTests] = useState<Record<string, any>>({});
  const blank = {
    name: '',
    country_code: 'SG',
    engine: 'DUCKDB',
    params: { ...ENGINE_DEFAULTS.DUCKDB },
    secret: { username: '', password: '' },
    is_authorized: true,
    allow_result_values_to_llm: false,
    limits_override: null,
  };

  async function save() {
    const body = {
      ...edit,
      params: Object.fromEntries(
        Object.entries(edit.params).filter(([, v]) => v !== '' && v != null),
      ),
    };
    if (!body.secret?.username && !body.secret?.password && !body.secret?.wallet_password)
      body.secret = edit.id ? null : {};
    const ok = await act(() =>
      edit.id ? put(`/admin/connections/${edit.id}`, body) : post('/admin/connections', body),
    );
    if (ok) {
      toast('Connection saved');
      setEdit(null);
      conns.reload();
    }
  }
  async function test(id: string) {
    setTests((t) => ({ ...t, [id]: { busy: true } }));
    const r = await act(() => post(`/admin/connections/${id}/test`));
    setTests((t) => ({ ...t, [id]: r || { ok: false } }));
    conns.reload();
  }

  return (
    <Section
      title="Connections"
      intro="Read-only connections to Oracle, MariaDB, PostgreSQL and DuckDB. Use a least-privilege account: the test warns if it can write."
      actions={
        <button className="btn btn-primary" onClick={() => setEdit(blank)}>
          Add connection
        </button>
      }
    >
      {conns.data?.items.length === 0 && (
        <Empty>No connections yet. Add one to start crawling its schema.</Empty>
      )}
      <div className="conn-cards">
        {conns.data?.items.map((c: any) => {
          const t = tests[c.id];
          return (
            <Card key={c.id} className="conn-card">
              <div className="cc-head">
                <strong>{c.name}</strong>
                <span className="pill pill-info">{c.engine.toLowerCase()}</span>
                <span className="pill">{c.country_code}</span>
                {c.is_authorized ? (
                  <span className="pill pill-ok">authorised</span>
                ) : (
                  <span className="pill pill-warn">not authorised</span>
                )}
              </div>
              <p className="small muted">
                Last test: {c.last_test_status || 'never'}{' '}
                {c.last_tested_at ? `(${fmtDate(c.last_tested_at)})` : ''}
                <br />
                Schema:{' '}
                {c.locked_snapshot ? `locked v${c.locked_snapshot.version}` : 'not locked yet'}
                <br />
                Result values to AI:{' '}
                {c.allow_result_values_to_llm ? 'allowed (non-sensitive columns)' : 'profile only'}
              </p>
              {t && !t.busy && (
                <p className={`small ${t.ok ? '' : 'warn-note'}`}>
                  {t.ok
                    ? `Connected in ${t.latency_ms} ms. ${t.server_version || ''}`
                    : t.error || 'Test failed'}
                  {t.warning && <span className="warn-note"> {t.warning}</span>}
                </p>
              )}
              <div className="row-actions">
                <button className="btn btn-sm" onClick={() => test(c.id)} disabled={t?.busy}>
                  {t?.busy ? 'Testing…' : 'Test'}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    setEdit({
                      ...c,
                      params: { ...c.params },
                      secret: { username: '', password: '' },
                    })
                  }
                >
                  Edit
                </button>
                {c.engine === 'ORACLE' && (
                  <label className="btn btn-sm">
                    Upload wallet
                    <input
                      type="file"
                      hidden
                      accept=".zip"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (!f) return;
                        const fd = new FormData();
                        fd.append('file', f);
                        act(
                          () =>
                            api(`/admin/connections/${c.id}/wallet`, { method: 'POST', body: fd }),
                          () => toast('Wallet stored (encrypted)'),
                        );
                      }}
                    />
                  </label>
                )}
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() =>
                    confirm(`Delete ${c.name}? Its schema snapshots and grants are removed too.`) &&
                    act(
                      () => del(`/admin/connections/${c.id}`),
                      () => conns.reload(),
                    )
                  }
                >
                  Delete
                </button>
              </div>
            </Card>
          );
        })}
      </div>
      {edit && (
        <Modal
          title={edit.id ? `Edit ${edit.name}` : 'Add connection'}
          onClose={() => setEdit(null)}
          wide
        >
          <div className="adm-form">
            <Field label="Name">
              <input
                className="input"
                value={edit.name}
                onChange={(e) => setEdit({ ...edit, name: e.target.value })}
              />
            </Field>
            <Field label="Country">
              <Select
                className="select"
                value={edit.country_code}
                onChange={(e) => setEdit({ ...edit, country_code: e.target.value })}
              >
                {countries.data?.items.map((k: any) => (
                  <option key={k.code} value={k.code}>
                    {k.name} ({k.code})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Engine">
              <Select
                className="select"
                value={edit.engine}
                disabled={!!edit.id}
                onChange={(e) =>
                  setEdit({
                    ...edit,
                    engine: e.target.value,
                    params: { ...ENGINE_DEFAULTS[e.target.value] },
                  })
                }
              >
                <option value="DUCKDB">DuckDB</option>
                <option value="POSTGRES">PostgreSQL</option>
                <option value="MARIADB">MariaDB</option>
                <option value="ORACLE">Oracle</option>
              </Select>
            </Field>
            {ENGINE_FIELDS[edit.engine].map((f) => (
              <Field key={f.k} label={f.label} hint={f.hint} wide={f.k === 'path'}>
                <input
                  className="input"
                  type={f.type || 'text'}
                  value={edit.params[f.k] ?? ''}
                  onChange={(e) =>
                    setEdit({
                      ...edit,
                      params: {
                        ...edit.params,
                        [f.k]: f.type === 'number' ? Number(e.target.value) : e.target.value,
                      },
                    })
                  }
                />
              </Field>
            ))}
            {edit.engine !== 'DUCKDB' && (
              <>
                <Field
                  label="Username"
                  hint={edit.id ? 'Leave blank to keep the saved credentials' : undefined}
                >
                  <input
                    className="input"
                    value={edit.secret.username}
                    onChange={(e) =>
                      setEdit({ ...edit, secret: { ...edit.secret, username: e.target.value } })
                    }
                    autoComplete="off"
                  />
                </Field>
                <Field label="Password">
                  <input
                    className="input"
                    type="password"
                    value={edit.secret.password}
                    onChange={(e) =>
                      setEdit({ ...edit, secret: { ...edit.secret, password: e.target.value } })
                    }
                    autoComplete="new-password"
                  />
                </Field>
              </>
            )}
            {edit.engine === 'ORACLE' && edit.params.mode === 'wallet' && (
              <Field label="Wallet password">
                <input
                  className="input"
                  type="password"
                  value={edit.secret.wallet_password || ''}
                  onChange={(e) =>
                    setEdit({
                      ...edit,
                      secret: { ...edit.secret, wallet_password: e.target.value },
                    })
                  }
                />
              </Field>
            )}
            <Field label="Row limit before confirmation" hint="Blank uses the global setting">
              <input
                className="input"
                type="number"
                value={edit.limits_override?.max_rows ?? ''}
                onChange={(e) =>
                  setEdit({
                    ...edit,
                    limits_override: {
                      ...edit.limits_override,
                      max_rows: e.target.value ? Number(e.target.value) : undefined,
                    },
                  })
                }
              />
            </Field>
            <Field label="Column limit before confirmation">
              <input
                className="input"
                type="number"
                value={edit.limits_override?.max_cols ?? ''}
                onChange={(e) =>
                  setEdit({
                    ...edit,
                    limits_override: {
                      ...edit.limits_override,
                      max_cols: e.target.value ? Number(e.target.value) : undefined,
                    },
                  })
                }
              />
            </Field>
            <Toggle
              checked={edit.is_authorized}
              onChange={(v) => setEdit({ ...edit, is_authorized: v })}
              label="Authorised for analysts"
            />
            <Toggle
              checked={edit.allow_result_values_to_llm}
              onChange={(v) => setEdit({ ...edit, allow_result_values_to_llm: v })}
              label="Send sample result values (non-sensitive columns) to the AI"
            />
          </div>
          <div className="modal-actions">
            <button className="btn" onClick={() => setEdit(null)}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={save} disabled={!edit.name}>
              Save connection
            </button>
          </div>
        </Modal>
      )}
    </Section>
  );
}

export function GuidelinesSection() {
  const g = useLoad<any>('/admin/guidelines');
  const conns = useLoad<any>('/admin/connections');
  const countries = useLoad<any>('/admin/countries');
  const [edit, setEdit] = useState<any>(null);
  const target = (x: any) =>
    x.connection_id
      ? (conns.data?.items.find((c: any) => c.id === x.connection_id)?.name ?? 'connection')
      : `Country ${x.country_code}`;
  return (
    <Section
      title="Guidelines"
      intro="Short, authoritative rules the AI always follows for a country or database, such as fiscal-year definitions or which status counts as revenue."
      actions={
        <button
          className="btn btn-primary"
          onClick={() =>
            setEdit({
              title: '',
              body_md: '',
              priority: 100,
              is_active: true,
              country_code: 'SG',
              connection_id: null,
            })
          }
        >
          Add guideline
        </button>
      }
    >
      {g.data?.items.length === 0 && <Empty>No guidelines yet.</Empty>}
      <div className="conn-cards">
        {g.data?.items.map((x: any) => (
          <Card key={x.id}>
            <div className="cc-head">
              <strong>{x.title}</strong>
              <span className="pill">{target(x)}</span>
              {!x.is_active && <span className="pill pill-warn">off</span>}
            </div>
            <p className="small">{x.body_md}</p>
            <div className="row-actions">
              <button className="btn btn-sm" onClick={() => setEdit(x)}>
                Edit
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() =>
                  act(
                    () => del(`/admin/guidelines/${x.id}`),
                    () => g.reload(),
                  )
                }
              >
                Delete
              </button>
            </div>
          </Card>
        ))}
      </div>
      {edit && (
        <Modal
          title={edit.id ? 'Edit guideline' : 'Add guideline'}
          onClose={() => setEdit(null)}
          wide
        >
          <div className="adm-form">
            <Field label="Title" wide>
              <input
                className="input"
                value={edit.title}
                onChange={(e) => setEdit({ ...edit, title: e.target.value })}
              />
            </Field>
            <Field label="Applies to">
              <Select
                className="select"
                value={edit.connection_id ? `c:${edit.connection_id}` : `k:${edit.country_code}`}
                onChange={(e) => {
                  const [t, v] = e.target.value.split(':');
                  setEdit({
                    ...edit,
                    connection_id: t === 'c' ? v : null,
                    country_code: t === 'k' ? v : null,
                  });
                }}
              >
                {countries.data?.items.map((k: any) => (
                  <option key={k.code} value={`k:${k.code}`}>
                    Country {k.name}
                  </option>
                ))}
                {conns.data?.items.map((c: any) => (
                  <option key={c.id} value={`c:${c.id}`}>
                    Database {c.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Priority" hint="Lower numbers come first">
              <input
                className="input"
                type="number"
                value={edit.priority}
                onChange={(e) => setEdit({ ...edit, priority: Number(e.target.value) })}
              />
            </Field>
            <Field label="Rule" wide>
              <textarea
                className="textarea"
                rows={5}
                value={edit.body_md}
                onChange={(e) => setEdit({ ...edit, body_md: e.target.value })}
              />
            </Field>
            <Toggle
              checked={edit.is_active}
              onChange={(v) => setEdit({ ...edit, is_active: v })}
              label="Active"
            />
          </div>
          <div className="modal-actions">
            <button className="btn" onClick={() => setEdit(null)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={() =>
                act(
                  () =>
                    edit.id
                      ? put(`/admin/guidelines/${edit.id}`, edit)
                      : post('/admin/guidelines', edit),
                  () => {
                    setEdit(null);
                    g.reload();
                  },
                )
              }
            >
              Save guideline
            </button>
          </div>
        </Modal>
      )}
    </Section>
  );
}
