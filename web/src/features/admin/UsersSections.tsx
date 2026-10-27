import { Select } from '../../lib/Select';
import { useState } from 'react';
import { patch, post, put } from '../../api/client';
import { toast } from '../../lib/toast';
import { act, Card, Field, fmtDate, Modal, Section, Toggle, useLoad } from './kit';

export function UsersSection() {
  const [q, setQ] = useState('');
  const users = useLoad<any>(`/admin/users${q ? `?q=${encodeURIComponent(q)}` : ''}`, [q]);
  const conns = useLoad<any>('/admin/connections');
  const countries = useLoad<any>('/admin/countries');
  const [creating, setCreating] = useState(false);
  const [grantsFor, setGrantsFor] = useState<any>(null);
  const [secret, setSecret] = useState<{ user: string; pw: string } | null>(null);
  const [form, setForm] = useState({
    user_id: '',
    display_name: '',
    email: '',
    role: 'ANALYST',
    auth_source: 'LOCAL',
  });

  return (
    <Section
      title="Users & roles"
      intro="Local and directory (LDAP) users, their role, and which databases they can query."
      actions={
        <>
          <input
            className="input"
            placeholder="Search users"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            Add user
          </button>
        </>
      }
    >
      <table className="adm-table">
        <thead>
          <tr>
            <th>User ID</th>
            <th>Name</th>
            <th>Role</th>
            <th>Sign-in</th>
            <th>Access</th>
            <th>Last sign-in</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {users.data?.items.map((u: any) => (
            <tr key={u.id} className={!u.is_active ? 'inactive' : ''}>
              <td className="mono">{u.user_id}</td>
              <td>{u.display_name}</td>
              <td>
                <Select
                  className="select"
                  value={u.role}
                  disabled={u.role_from_ldap}
                  title={u.role_from_ldap ? 'Managed by LDAP group mapping' : ''}
                  onChange={(e) =>
                    act(
                      () => patch(`/admin/users/${u.id}`, { role: e.target.value }),
                      () => users.reload(),
                    )
                  }
                >
                  <option value="ANALYST">Analyst</option>
                  <option value="SYSTEM_ADMIN">Administrator</option>
                </Select>
              </td>
              <td>
                <span className="pill">{u.auth_source.toLowerCase()}</span>{' '}
                {u.locked && <span className="pill pill-bad">locked</span>}{' '}
                {!u.is_active && <span className="pill pill-warn">inactive</span>}
              </td>
              <td className="small">
                {u.role === 'SYSTEM_ADMIN'
                  ? 'All databases'
                  : [
                      ...u.grants.countries.map((c: string) => `Country ${c}`),
                      ...u.grants.connections.map(
                        (id: string) =>
                          conns.data?.items.find((x: any) => x.id === id)?.name || id.slice(0, 8),
                      ),
                    ].join(', ') || 'None'}
              </td>
              <td className="small">{fmtDate(u.last_login_at)}</td>
              <td className="row-actions">
                <button className="btn btn-sm" onClick={() => setGrantsFor(u)}>
                  Access
                </button>
                {u.auth_source === 'LOCAL' && (
                  <button
                    className="btn btn-sm"
                    onClick={() =>
                      act(
                        () => post(`/admin/users/${u.id}/reset-password`),
                        (r: any) => setSecret({ user: u.user_id, pw: r.temporary_password }),
                      )
                    }
                  >
                    Reset password
                  </button>
                )}
                {u.locked && (
                  <button
                    className="btn btn-sm"
                    onClick={() =>
                      act(
                        () => post(`/admin/users/${u.id}/unlock`),
                        () => users.reload(),
                      )
                    }
                  >
                    Unlock
                  </button>
                )}
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() =>
                    act(
                      () => patch(`/admin/users/${u.id}`, { is_active: !u.is_active }),
                      () => users.reload(),
                    )
                  }
                >
                  {u.is_active ? 'Deactivate' : 'Activate'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {creating && (
        <Modal title="Add user" onClose={() => setCreating(false)}>
          <div className="adm-form">
            <Field label="User ID">
              <input
                className="input"
                value={form.user_id}
                onChange={(e) => setForm({ ...form, user_id: e.target.value })}
              />
            </Field>
            <Field label="Display name">
              <input
                className="input"
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              />
            </Field>
            <Field label="Email">
              <input
                className="input"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </Field>
            <Field label="Role">
              <Select
                className="select"
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                <option value="ANALYST">Analyst</option>
                <option value="SYSTEM_ADMIN">Administrator</option>
              </Select>
            </Field>
            <Field
              label="Sign-in"
              hint="Local users get a temporary password and must change it at first sign-in."
            >
              <Select
                className="select"
                value={form.auth_source}
                onChange={(e) => setForm({ ...form, auth_source: e.target.value })}
              >
                <option value="LOCAL">Local password</option>
                <option value="LDAP">Directory (LDAP)</option>
              </Select>
            </Field>
          </div>
          <div className="modal-actions">
            <button className="btn" onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={() =>
                act(
                  () => post('/admin/users', { ...form, email: form.email || null }),
                  (r: any) => {
                    setCreating(false);
                    users.reload();
                    if (r.temporary_password)
                      setSecret({ user: form.user_id, pw: r.temporary_password });
                  },
                )
              }
            >
              Add user
            </button>
          </div>
        </Modal>
      )}
      {grantsFor && (
        <GrantsModal
          user={grantsFor}
          conns={conns.data?.items || []}
          countries={countries.data?.items || []}
          onClose={() => {
            setGrantsFor(null);
            users.reload();
          }}
        />
      )}
      {secret && (
        <Modal title="Temporary password" onClose={() => setSecret(null)}>
          <p>
            Give this one-time password to <strong>{secret.user}</strong>. It won't be shown again,
            and they'll pick a new one when they sign in.
          </p>
          <pre className="sqlcode secret">{secret.pw}</pre>
        </Modal>
      )}
    </Section>
  );
}

function GrantsModal({
  user,
  conns,
  countries,
  onClose,
}: {
  user: any;
  conns: any[];
  countries: any[];
  onClose: () => void;
}) {
  const [c, setC] = useState<string[]>(user.grants.connections);
  const [k, setK] = useState<string[]>(user.grants.countries);
  const flip = (arr: string[], v: string) =>
    arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
  return (
    <Modal title={`Database access for ${user.user_id}`} onClose={onClose} wide>
      <p className="muted">A country grant covers every authorised database in that country.</p>
      <div className="grant-grid">
        <Card>
          <h4>Countries</h4>
          {countries.map((x) => (
            <Toggle
              key={x.code}
              checked={k.includes(x.code)}
              onChange={() => setK(flip(k, x.code))}
              label={`${x.name} (${x.code})`}
            />
          ))}
        </Card>
        <Card>
          <h4>Individual databases</h4>
          {conns.length === 0 && <p className="muted small">No connections yet.</p>}
          {conns.map((x) => (
            <Toggle
              key={x.id}
              checked={c.includes(x.id)}
              onChange={() => setC(flip(c, x.id))}
              label={`${x.name} (${x.engine.toLowerCase()}, ${x.country_code})`}
            />
          ))}
        </Card>
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          onClick={() =>
            act(
              () => put(`/admin/users/${user.id}/grants`, { connections: c, countries: k }),
              () => {
                toast('Access saved');
                onClose();
              },
            )
          }
        >
          Save access
        </button>
      </div>
    </Modal>
  );
}

export function AuthSection() {
  const s = useLoad<any>('/admin/auth/settings');
  const [bindPw, setBindPw] = useState('');
  const [testUser, setTestUser] = useState('');
  const [test, setTest] = useState<any>(null);
  if (!s.data) return <Section title="Authentication">{s.error || 'Loading…'}</Section>;
  const v = s.data.values;
  const ldap = v.ldap || {};
  const setV = (k: string, val: any) => s.setData({ ...s.data, values: { ...v, [k]: val } });
  const setL = (k: string, val: any) => setV('ldap', { ...ldap, [k]: val });
  const save = () =>
    act(
      () => put('/admin/auth/settings', { values: v, ldap_bind_password: bindPw || null }),
      () => {
        toast('Authentication settings saved');
        setBindPw('');
        s.reload();
      },
    );
  const num = (k: string, label: string, hint?: string) => (
    <Field label={label} hint={hint}>
      <input
        className="input"
        type="number"
        value={v[k]}
        onChange={(e) => setV(k, Number(e.target.value))}
      />
    </Field>
  );
  const lt = (k: string, label: string, hint?: string) => (
    <Field label={label} hint={hint} wide>
      <input
        className="input"
        value={Array.isArray(ldap[k]) ? ldap[k].join(', ') : (ldap[k] ?? '')}
        onChange={(e) =>
          setL(
            k,
            Array.isArray(ldap[k])
              ? e.target.value
                  .split(',')
                  .map((x) => x.trim())
                  .filter(Boolean)
              : e.target.value,
          )
        }
      />
    </Field>
  );
  return (
    <Section
      title="Authentication"
      intro="Password policy, lockout, session timeouts and the LDAP directory."
      actions={
        <button className="btn btn-primary" onClick={save}>
          Save changes
        </button>
      }
    >
      <Card>
        <h4>Sign-in methods</h4>
        <div className="adm-form">
          <Toggle
            checked={v['auth.local_enabled']}
            onChange={(x) => setV('auth.local_enabled', x)}
            label="Local user ID and password"
          />
          <Toggle
            checked={v['auth.ldap_enabled']}
            onChange={(x) => {
              setV('auth.ldap_enabled', x);
            }}
            label="LDAP directory"
          />
        </div>
      </Card>
      <Card>
        <h4>Password policy and sessions</h4>
        <div className="adm-form">
          {num('auth.password_min_length', 'Minimum length')}
          {num('auth.password_history', 'Remember last N passwords')}
          {num('auth.password_expiry_days', 'Expire after (days)', '0 turns expiry off')}
          {num('auth.lockout_threshold', 'Lock after failed attempts')}
          {num('auth.lockout_minutes', 'Lock for (minutes)')}
          {num('auth.session_idle_min', 'Idle timeout (minutes)')}
          {num('auth.session_absolute_h', 'Absolute timeout (hours)')}
        </div>
      </Card>
      <Card>
        <h4>LDAP directory</h4>
        <div className="adm-form">
          {lt(
            'server_urls',
            'Server URLs',
            'Comma-separated; tried in order for failover, e.g. ldaps://dc1.corp:636',
          )}
          <Field label="Bind mode">
            <Select
              className="select"
              value={ldap.bind_mode}
              onChange={(e) => setL('bind_mode', e.target.value)}
            >
              <option value="search_then_bind">Search, then bind as user</option>
              <option value="direct_bind">Direct bind</option>
            </Select>
          </Field>
          <Toggle
            checked={!!ldap.use_starttls}
            onChange={(x) => setL('use_starttls', x)}
            label="Use StartTLS (ldap:// on 389)"
          />
          <Toggle
            checked={ldap.verify_ssl !== false}
            onChange={(x) => setL('verify_ssl', x)}
            label="Verify TLS certificate"
          />
          {ldap.verify_ssl === false && (
            <p className="warn-note">
              TLS verification is off. Connections to the directory can be intercepted.
            </p>
          )}
          {lt('ca_cert_path', 'CA bundle path')}
          {lt('base_dn', 'Base DN')}
          {lt('bind_dn', 'Service account DN')}
          <Field
            label="Service account password"
            hint={
              s.data.ldap_bind_password_set
                ? 'A password is saved. Type a new one to replace it.'
                : 'Not set'
            }
            wide
          >
            <input
              className="input"
              type="password"
              value={bindPw}
              onChange={(e) => setBindPw(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
          {lt('user_filter', 'User filter', '{user_id} is replaced (and escaped) at sign-in')}
          {lt('user_dn_template', 'User DN template (direct bind)')}
          {lt('attr_display_name', 'Display-name attribute')}
          {lt('attr_email', 'Email attribute')}
          {lt('group_attr', 'Group attribute')}
          {lt('admin_groups', 'Administrator groups (DNs)')}
          {lt(
            'analyst_groups',
            'Analyst groups (DNs)',
            'Empty lets any directory user sign in as an analyst',
          )}
          <Toggle
            checked={ldap.auto_provision !== false}
            onChange={(x) => setL('auto_provision', x)}
            label="Create users on first sign-in"
          />
        </div>
        <div className="adm-inline">
          <input
            className="input"
            placeholder="Test user ID (optional)"
            value={testUser}
            onChange={(e) => setTestUser(e.target.value)}
          />
          <button
            className="btn"
            onClick={() =>
              act(() => post('/admin/auth/ldap/test', { test_user: testUser || null }), setTest)
            }
          >
            Test directory
          </button>
          {test && (
            <span className={`pill ${test.ok ? 'pill-ok' : 'pill-bad'}`}>
              {test.ok
                ? `Connected${test.found ? `: found ${test.found.length}` : ''}`
                : test.detail}
            </span>
          )}
        </div>
      </Card>
    </Section>
  );
}
