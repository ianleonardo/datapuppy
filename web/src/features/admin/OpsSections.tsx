import { Select } from '../../lib/Select';
import { Fragment, useState } from 'react';
import { post, waitJob } from '../../api/client';
import { useApp } from '../../app/store';
import { toast } from '../../lib/toast';
import { Mascot } from '../../mascot/Mascot';
import { act, Card, Empty, Field, fmtBytes, fmtDate, Modal, Section, useLoad } from './kit';

export function SystemSection() {
  const s = useLoad<any>('/admin/system');
  const jobs = useLoad<any>('/admin/jobs');
  const d = s.data;
  return (
    <Section
      title="System"
      intro="Paths, storage, background jobs and health."
      actions={
        <>
          <button
            className="btn"
            onClick={() =>
              act(
                () => post('/admin/system/verify-audit'),
                async (r: any) => {
                  const j = await waitJob(r.job_id);
                  toast(
                    j.result?.ok
                      ? 'Audit chain verified'
                      : 'Audit chain problems found — see the banner',
                  );
                  s.reload();
                },
              )
            }
          >
            Verify audit chain
          </button>
          <button
            className="btn"
            onClick={() =>
              act(
                () => post('/admin/system/compact'),
                () => {
                  toast('Compaction will run at the next restart');
                  s.reload();
                },
              )
            }
          >
            Compact database
          </button>
          <button
            className="btn btn-primary"
            onClick={() =>
              act(
                () => post('/admin/system/backup'),
                async (r: any) => {
                  toast('Backup started');
                  await waitJob(r.job_id);
                  toast('Backup finished');
                  s.reload();
                  jobs.reload();
                },
              )
            }
          >
            Back up now
          </button>
        </>
      }
    >
      {d?.audit_alert && !d.audit_alert.ok && (
        <p className="warn-note">
          Audit chain verification failed: {d.audit_alert.problems?.slice(0, 3).join('; ')}
        </p>
      )}
      {d && (
        <div className="stat-cards">
          <Card>
            <h4>Storage</h4>
            <dl className="kv small">
              <dt>Data folder</dt>
              <dd className="mono">{d.paths.data_dir}</dd>
              <dt>Log folder</dt>
              <dd className="mono">{d.paths.log_dir}</dd>
              <dt>Config file</dt>
              <dd className="mono">{d.paths.config_file || 'defaults only'}</dd>
              <dt>Database size</dt>
              <dd>{fmtBytes(d.db_size_bytes)}</dd>
              <dt>Free disk</dt>
              <dd>
                {fmtBytes(d.disk_free_bytes)} of {fmtBytes(d.disk_total_bytes)}
              </dd>
              <dt>Last backup</dt>
              <dd>
                {d.last_backup
                  ? `${d.last_backup.file} (${fmtBytes(d.last_backup.size)})`
                  : 'none yet'}
              </dd>
              <dt>Compaction</dt>
              <dd>{d.compaction_pending ? 'pending restart' : '—'}</dd>
            </dl>
          </Card>
          <Card>
            <h4>Runtime</h4>
            <dl className="kv small">
              <dt>Uptime</dt>
              <dd>
                {Math.floor(d.uptime_s / 3600)} h {Math.floor((d.uptime_s % 3600) / 60)} min
              </dd>
              <dt>Job runner</dt>
              <dd>
                {d.runner_alive ? 'running' : 'stopped'}, {d.queue_depth} queued
              </dd>
              <dt>Keyword search (fts)</dt>
              <dd>{d.extensions.fts ? 'available' : 'unavailable, using fallback'}</dd>
              <dt>Vector index (vss)</dt>
              <dd>{d.extensions.vss ? 'available' : 'unavailable, exact search'}</dd>
              <dt>Log level</dt>
              <dd>{d.log_level}</dd>
              <dt>AI errors (last hour)</dt>
              <dd>
                {Object.entries(d.provider_errors_1h)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(', ') || 'none'}
              </dd>
            </dl>
          </Card>
          <Card>
            <h4>Answer quality (7 days)</h4>
            <dl className="kv small">
              <dt>AI queries run</dt>
              <dd>{d.quality.ai_runs_7d}</dd>
              <dt>Ran without error</dt>
              <dd>
                {d.quality.ai_success_rate_7d == null
                  ? '—'
                  : `${Math.round(d.quality.ai_success_rate_7d * 100)}%`}
              </dd>
              <dt>Thumbs up / down</dt>
              <dd>
                {d.quality.thumbs_up_7d} / {d.quality.thumbs_down_7d}
              </dd>
            </dl>
          </Card>
        </div>
      )}
      <Card>
        <h4>Recent jobs</h4>
        <table className="adm-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>Created</th>
              <th>Finished</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data?.items.map((j: any) => (
              <tr key={j.id}>
                <td className="mono">{j.type}</td>
                <td>
                  <span
                    className={`pill ${j.status === 'SUCCEEDED' ? 'pill-ok' : j.status === 'FAILED' ? 'pill-bad' : 'pill-warn'}`}
                  >
                    {j.status.toLowerCase()}
                  </span>
                </td>
                <td className="small">{fmtDate(j.created_at)}</td>
                <td className="small">{fmtDate(j.finished_at)}</td>
                <td className="small">{j.error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Section>
  );
}

export function AuditSection() {
  const [f, setF] = useState({ action: '', status: '', user_id: '', date_from: '', date_to: '' });
  const qs = new URLSearchParams(
    Object.entries(f).filter(([, v]) => v) as [string, string][],
  ).toString();
  const a = useLoad<any>(`/admin/audit${qs ? `?${qs}` : ''}`, [qs]);
  const [open, setOpen] = useState<any>(null);
  return (
    <Section
      title="Audit log"
      intro="Every sign-in, upload, connection change, query and purge. The log is hash-chained and mirrored to daily files."
      actions={
        <a className="btn" href={`/api/v1/admin/audit/export.csv${qs ? `?${qs}` : ''}`}>
          Export CSV
        </a>
      }
    >
      <Card>
        <div className="adm-inline">
          <Select
            className="select"
            value={f.action}
            onChange={(e) => setF({ ...f, action: e.target.value })}
          >
            <option value="">All actions</option>
            {a.data?.actions.map((x: string) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
          <Select
            className="select"
            value={f.status}
            onChange={(e) => setF({ ...f, status: e.target.value })}
          >
            <option value="">All outcomes</option>
            <option>SUCCESS</option>
            <option>FAILED</option>
            <option>INTERCEPTED</option>
          </Select>
          <input
            className="input"
            placeholder="User ID"
            value={f.user_id}
            onChange={(e) => setF({ ...f, user_id: e.target.value })}
          />
          <input
            className="input"
            type="date"
            value={f.date_from}
            onChange={(e) => setF({ ...f, date_from: e.target.value })}
            aria-label="From"
          />
          <input
            className="input"
            type="date"
            value={f.date_to}
            onChange={(e) => setF({ ...f, date_to: e.target.value })}
            aria-label="To"
          />
        </div>
      </Card>
      <table className="adm-table">
        <thead>
          <tr>
            <th>Time (UTC)</th>
            <th>User</th>
            <th>Action</th>
            <th>Outcome</th>
            <th>Context</th>
          </tr>
        </thead>
        <tbody>
          {a.data?.items.map((r: any) => (
            <tr key={r.id} onClick={() => setOpen(r)} className="clickable">
              <td className="small mono">{fmtDate(r.ts)}</td>
              <td>{r.user_id || '—'}</td>
              <td className="mono small">{r.action}</td>
              <td>
                <span
                  className={`pill ${r.status === 'SUCCESS' ? 'pill-ok' : r.status === 'FAILED' ? 'pill-bad' : 'pill-warn'}`}
                >
                  {r.status.toLowerCase()}
                </span>
              </td>
              <td className="small ctx-cell">
                {r.context.sql ||
                  r.context.filename ||
                  r.context.reason ||
                  r.context.name ||
                  r.context.op ||
                  JSON.stringify(r.context).slice(0, 90)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {a.data?.items.length === 0 && <Empty>No audit events match these filters.</Empty>}
      {open && (
        <Modal title={`${open.action} #${open.id}`} onClose={() => setOpen(null)} wide>
          <pre className="sqlcode" style={{ whiteSpace: 'pre-wrap' }}>
            {JSON.stringify(open, null, 2)}
          </pre>
        </Modal>
      )}
    </Section>
  );
}

export function ChatMetaSection() {
  const c = useLoad<any>('/admin/chat-metadata');
  return (
    <Section
      title="Chat activity"
      intro="Session metadata only. Administrators can't read the content of other people's chats."
    >
      <table className="adm-table">
        <thead>
          <tr>
            <th>Owner</th>
            <th>Session</th>
            <th>Messages</th>
            <th>Started</th>
            <th>Last activity</th>
          </tr>
        </thead>
        <tbody>
          {c.data?.items.map((r: any) => (
            <tr key={r.session_id}>
              <td>{r.owner}</td>
              <td className="mono small">{String(r.session_id).slice(0, 8)}</td>
              <td className="num">{r.message_count}</td>
              <td className="small">{fmtDate(r.created_at)}</td>
              <td className="small">{fmtDate(r.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.data?.items.length === 0 && <Empty>No chats yet.</Empty>}
    </Section>
  );
}

export function PurgeSection() {
  const theme = useApp((s) => s.me!.theme);
  const [scope, setScope] = useState('AUDIT');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [pv, setPv] = useState<any>(null);
  const [confirmText, setConfirmText] = useState('');
  const [running, setRunning] = useState(false);
  const [summary, setSummary] = useState<any>(null);

  async function execute() {
    setRunning(true);
    const r = await act(() =>
      post('/admin/purge/execute', {
        scope,
        date_from: from || null,
        date_to: to,
        confirm: confirmText,
      }),
    );
    if (r) {
      const j = await waitJob((r as any).job_id);
      if (j.status === 'SUCCEEDED') setSummary(j.result);
      else toast(`Purge failed: ${j.error}`);
    }
    setRunning(false);
    setPv(null);
    setConfirmText('');
  }

  return (
    <Section
      title="Purge"
      intro="Permanently delete old audit records, chat histories, or both. Audit purges always start from the oldest record so the hash chain stays verifiable."
    >
      <Card>
        <div className="adm-form">
          <Field label="What to purge">
            <Select
              className="select"
              value={scope}
              onChange={(e) => {
                setScope(e.target.value);
                setPv(null);
              }}
            >
              <option value="AUDIT">Audit records</option>
              <option value="CHAT">Chat histories (all users)</option>
              <option value="BOTH">Both</option>
            </Select>
          </Field>
          <Field
            label="From"
            hint={
              scope === 'AUDIT'
                ? 'Audit purges always begin at the earliest record'
                : 'Chats last active on or after this day'
            }
          >
            <input
              className="input"
              type="date"
              value={scope === 'AUDIT' ? '' : from}
              disabled={scope === 'AUDIT'}
              onChange={(e) => setFrom(e.target.value)}
            />
          </Field>
          <Field label="Up to and including">
            <input
              className="input"
              type="date"
              value={to}
              onChange={(e) => {
                setTo(e.target.value);
                setPv(null);
              }}
            />
          </Field>
        </div>
        <div className="modal-actions">
          <button
            className="btn"
            disabled={!to}
            onClick={() =>
              act(
                () => post('/admin/purge/preview', { scope, date_from: from || null, date_to: to }),
                setPv,
              )
            }
          >
            Preview
          </button>
        </div>
      </Card>
      {pv && (
        <Card className="purge-preview">
          <Mascot theme={theme} mood="protective" size={100} />
          <div>
            <p>
              This permanently deletes
              {pv.audit_rows != null && (
                <>
                  {' '}
                  <strong>{pv.audit_rows.toLocaleString()}</strong> audit records (from{' '}
                  {pv.audit_from || 'the beginning'})
                </>
              )}
              {pv.chat_sessions != null && (
                <>
                  {' '}
                  {pv.audit_rows != null && 'and'}{' '}
                  <strong>{pv.chat_sessions.toLocaleString()}</strong> chats with{' '}
                  <strong>{pv.chat_messages.toLocaleString()}</strong> messages
                </>
              )}{' '}
              up to {pv.date_to}. It can't be undone.
            </p>
            <div className="adm-inline">
              <input
                className="input"
                placeholder="Type PURGE to confirm"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
              />
              <button
                className="btn btn-danger"
                disabled={confirmText !== 'PURGE' || running}
                onClick={execute}
              >
                {running ? 'Purging…' : 'Purge records'}
              </button>
            </div>
          </div>
        </Card>
      )}
      {summary && (
        <Modal title="Purge complete" onClose={() => setSummary(null)}>
          <dl className="kv">
            {Object.entries(summary.removed).map(([k, v]) => (
              <Fragment key={`r${k}`}>
                <dt>Removed {k.replace('_', ' ')}</dt>
                <dd>{String(v)}</dd>
              </Fragment>
            ))}
            {Object.entries(summary.remaining).map(([k, v]) => (
              <Fragment key={`m${k}`}>
                <dt>Remaining {k.replace('_', ' ')}</dt>
                <dd>{String(v)}</dd>
              </Fragment>
            ))}
            <dt>Database size</dt>
            <dd>
              {fmtBytes(summary.db_size_before)} → {fmtBytes(summary.db_size_after)}
            </dd>
            <dt>Free disk</dt>
            <dd>{fmtBytes(summary.disk_free_bytes)}</dd>
          </dl>
          <p className="small muted">{summary.note}</p>
        </Modal>
      )}
    </Section>
  );
}
