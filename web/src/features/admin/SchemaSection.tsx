import { Select } from '../../lib/Select';
import { useEffect, useMemo, useState } from 'react';
import { patch, post, waitJob } from '../../api/client';
import { useApp } from '../../app/store';
import { toast } from '../../lib/toast';
import { Mascot } from '../../mascot/Mascot';
import { act, Card, Empty, Modal, Section, useLoad } from './kit';

const CLASSES = ['FACT', 'DIMENSION', 'STAGING', 'BACKUP', 'TEMP', 'ARCHIVE', 'UNKNOWN'];
const INCL_PILL: Record<string, string> = {
  INCLUDED: 'pill-ok',
  EXCLUDED: 'pill-bad',
  PENDING_REVIEW: 'pill-warn',
};

/** Crawl → review (smelly tables, aliases, sensitivity, relationships) → lock (TS §10). */
export function SchemaSection() {
  const theme = useApp((s) => s.me!.theme);
  const conns = useLoad<any>('/admin/connections');
  const [cid, setCid] = useState<string>('');
  const snaps = useLoad<any>(cid ? `/admin/schema/${cid}/snapshots` : null, [cid]);
  const [sid, setSid] = useState<string>('');
  const snap = useLoad<any>(sid ? `/admin/schema/snapshots/${sid}` : null, [sid]);
  const [crawl, setCrawl] = useState<any>(null);
  const [filter, setFilter] = useState<'all' | 'pending' | 'smelly' | 'excluded'>('all');
  const [openTable, setOpenTable] = useState<any>(null);
  const [rel, setRel] = useState<any>(null);
  const [diff, setDiff] = useState<any>(null);

  useEffect(() => {
    if (!cid && conns.data?.items?.length) setCid(conns.data.items[0].id);
  }, [conns.data, cid]);
  useEffect(() => {
    const items = snaps.data?.items;
    if (items?.length)
      setSid(
        (
          items.find((s: any) => s.state === 'DRAFT') ||
          items.find((s: any) => s.state === 'LOCKED') ||
          items[0]
        ).id,
      );
    else setSid('');
  }, [snaps.data]);

  const tables = useMemo(() => {
    const all = snap.data?.tables || [];
    if (filter === 'pending') return all.filter((t: any) => t.inclusion === 'PENDING_REVIEW');
    if (filter === 'smelly') return all.filter((t: any) => t.smell_flags?.length);
    if (filter === 'excluded') return all.filter((t: any) => t.inclusion === 'EXCLUDED');
    return all;
  }, [snap.data, filter]);
  const pending = (snap.data?.tables || []).filter(
    (t: any) => t.inclusion === 'PENDING_REVIEW',
  ).length;
  const editable = snap.data?.state === 'DRAFT';

  async function startCrawl() {
    const r = await act(() => post(`/admin/schema/${cid}/crawl`, {}));
    if (!r) return;
    setCrawl({ status: 'QUEUED' });
    const j = await waitJob((r as any).job_id, (x) => setCrawl(x));
    setCrawl(j);
    if (j.status === 'SUCCEEDED')
      toast(`Crawl done: ${j.result.tables} tables, ${j.result.pending} need review`);
    snaps.reload();
  }
  const setTable = (t: any, body: any) =>
    act(
      () => patch(`/admin/schema/tables/${t.id}`, body),
      () => snap.reload(),
    );

  return (
    <Section
      title="Schema governance"
      intro="Crawl a database, check what the pup flagged, then lock the approved schema. Analysts and the AI only see locked, included tables."
      actions={
        <>
          <Select
            className="select"
            value={cid}
            onChange={(e) => setCid(e.target.value)}
            aria-label="Connection"
          >
            {conns.data?.items.map((c: any) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.engine.toLowerCase()}, {c.country_code})
              </option>
            ))}
          </Select>
          <button
            className="btn btn-primary"
            onClick={startCrawl}
            disabled={!cid || crawl?.status === 'RUNNING' || crawl?.status === 'QUEUED'}
          >
            {crawl && ['RUNNING', 'QUEUED'].includes(crawl.status) ? 'Crawling…' : 'Crawl schema'}
          </button>
        </>
      }
    >
      {crawl && ['RUNNING', 'QUEUED'].includes(crawl.status) && (
        <Card className="crawl-card">
          <Mascot theme={theme} mood="thinking" size={80} />
          <div>
            <strong>Sniffing through the catalog…</strong>
            <p className="small muted">
              {crawl.progress?.phase || 'queued'}{' '}
              {crawl.progress?.total ? `${crawl.progress.done}/${crawl.progress.total}` : ''}
            </p>
          </div>
        </Card>
      )}
      {crawl?.status === 'FAILED' && <p className="warn-note">Crawl failed: {crawl.error}</p>}
      {!conns.data?.items.length && <Empty>Add a connection first.</Empty>}
      {cid && snaps.data?.items.length === 0 && (
        <Empty>This database hasn't been crawled yet.</Empty>
      )}
      {snap.data && (
        <>
          <div className="snap-bar">
            <Select
              className="select"
              value={sid}
              onChange={(e) => setSid(e.target.value)}
              aria-label="Snapshot"
            >
              {snaps.data?.items.map((s: any) => (
                <option key={s.id} value={s.id}>
                  Version {s.version} ({s.state.toLowerCase()})
                </option>
              ))}
            </Select>
            <span className={`pill ${snap.data.state === 'LOCKED' ? 'pill-ok' : 'pill-warn'}`}>
              {snap.data.state.toLowerCase()}
            </span>
            {pending > 0 && (
              <span className="pill pill-warn">
                {pending} table{pending > 1 ? 's' : ''} to review
              </span>
            )}
            <div className="seg">
              {(['all', 'pending', 'smelly', 'excluded'] as const).map((f) => (
                <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
                  {f}
                </button>
              ))}
            </div>
            <span className="grow" />
            <button
              className="btn btn-sm"
              onClick={() =>
                act(
                  () =>
                    import('../../api/client').then((m) =>
                      m.get(`/admin/schema/snapshots/${sid}/diff`),
                    ),
                  setDiff,
                )
              }
            >
              Changes since last lock
            </button>
            {editable && (
              <button
                className="btn btn-sm"
                onClick={() =>
                  setRel({
                    from_table_id: '',
                    from_columns: '',
                    to_table_id: '',
                    to_columns: '',
                    name: '',
                  })
                }
              >
                Add relationship
              </button>
            )}
            {editable ? (
              <button
                className="btn btn-go btn-sm"
                disabled={pending > 0}
                title={pending ? 'Resolve every table pending review first' : ''}
                onClick={() =>
                  act(
                    () => post(`/admin/schema/snapshots/${sid}/lock`),
                    (r: any) => {
                      toast(
                        `Locked. ${r.cards} tables ${r.embedded ? 'embedded' : 'stored (no embeddings yet)'}`,
                      );
                      snaps.reload();
                    },
                  )
                }
              >
                Lock schema
              </button>
            ) : (
              snap.data.state === 'LOCKED' && (
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    act(
                      () => post(`/admin/schema/snapshots/${sid}/unlock`),
                      (r: any) => {
                        snaps.reload();
                        setSid(r.draft_id);
                      },
                    )
                  }
                >
                  Unlock to new draft
                </button>
              )
            )}
          </div>
          {pending > 0 && editable && (
            <div className="sniff-note">
              <Mascot theme={theme} mood="sniffing" size={70} />
              <p>
                I found tables whose names look like backups, temp work or dated copies. Decide
                whether each one should be hidden from analysts and the AI. Tables flagged as high
                risk are already hidden.
              </p>
              <button
                className="btn btn-sm"
                onClick={() =>
                  act(
                    () =>
                      post(`/admin/schema/snapshots/${sid}/bulk-inclusion`, {
                        table_ids: snap.data.tables
                          .filter((t: any) => t.inclusion === 'PENDING_REVIEW')
                          .map((t: any) => t.id),
                        inclusion: 'EXCLUDED',
                      }),
                    () => snap.reload(),
                  )
                }
              >
                Hide all pending
              </button>
            </div>
          )}
          <table className="adm-table">
            <thead>
              <tr>
                <th>Table</th>
                <th>Alias</th>
                <th>Class</th>
                <th>Flags</th>
                <th>Rows</th>
                <th>Visibility</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tables.map((t: any) => (
                <tr key={t.id} className={t.inclusion !== 'INCLUDED' ? 'dim' : ''}>
                  <td className="mono">
                    {t.schema_name}.{t.table_name}
                    {t.object_type !== 'TABLE' && <span className="pill">view</span>}
                    {t.base_table_hint && (
                      <div className="small muted">Looks like a copy of {t.base_table_hint}</div>
                    )}
                  </td>
                  <td>
                    <input
                      className="input input-sm"
                      defaultValue={t.alias || ''}
                      disabled={!editable}
                      onBlur={(e) =>
                        e.target.value !== (t.alias || '') &&
                        setTable(t, { alias: e.target.value || null })
                      }
                    />
                  </td>
                  <td>
                    <Select
                      className="select"
                      value={t.classification}
                      disabled={!editable}
                      onChange={(e) => setTable(t, { classification: e.target.value })}
                    >
                      {CLASSES.map((c) => (
                        <option key={c} value={c}>
                          {c.toLowerCase()}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="small">{t.smell_flags?.join(', ')}</td>
                  <td className="num">{t.row_estimate?.toLocaleString() ?? '—'}</td>
                  <td>
                    <Select
                      className={`select pill-select ${INCL_PILL[t.inclusion]}`}
                      value={t.inclusion}
                      disabled={!editable}
                      onChange={(e) => setTable(t, { inclusion: e.target.value })}
                    >
                      <option value="INCLUDED">included</option>
                      <option value="EXCLUDED">hidden</option>
                      <option value="PENDING_REVIEW">needs review</option>
                    </Select>
                  </td>
                  <td>
                    <button className="btn btn-sm" onClick={() => setOpenTable(t)}>
                      Columns
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {snap.data.relationships?.length > 0 && (
            <Card>
              <h4>Relationships</h4>
              <ul className="rel-list">
                {snap.data.relationships.map((r: any) => (
                  <li key={r.id}>
                    <span className="mono">
                      {r.from_table}({r.from_columns.join(', ')}) → {r.to_table}(
                      {r.to_columns.join(', ')})
                    </span>
                    <span className="pill">
                      {r.source === 'FK_CONSTRAINT' ? 'foreign key' : 'admin defined'}
                    </span>
                    {editable && r.source === 'ADMIN_DEFINED' && (
                      <button
                        className="btn btn-sm btn-ghost"
                        onClick={() =>
                          act(
                            () =>
                              import('../../api/client').then((m) =>
                                m.del(`/admin/schema/snapshots/${sid}/relationships/${r.id}`),
                              ),
                            () => snap.reload(),
                          )
                        }
                      >
                        Remove
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
      {openTable && (
        <Modal title={`${openTable.table_name} columns`} onClose={() => setOpenTable(null)} wide>
          <table className="adm-table">
            <thead>
              <tr>
                <th>Column</th>
                <th>Type</th>
                <th>Alias</th>
                <th>Description</th>
                <th>Sensitivity</th>
                <th>Hide from AI</th>
              </tr>
            </thead>
            <tbody>
              {openTable.columns.map((c: any) => (
                <tr key={c.id}>
                  <td className="mono">
                    {c.is_pk ? '🔑 ' : ''}
                    {c.column_name}
                  </td>
                  <td className="small mono">{c.data_type}</td>
                  <td>
                    <input
                      className="input input-sm"
                      defaultValue={c.alias || ''}
                      disabled={!editable}
                      onBlur={(e) =>
                        act(() =>
                          patch(`/admin/schema/columns/${c.id}`, { alias: e.target.value || null }),
                        )
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="input input-sm"
                      defaultValue={c.description || ''}
                      disabled={!editable}
                      onBlur={(e) =>
                        act(() =>
                          patch(`/admin/schema/columns/${c.id}`, {
                            description: e.target.value || null,
                          }),
                        )
                      }
                    />
                  </td>
                  <td>
                    <Select
                      className="select"
                      defaultValue={c.sensitivity}
                      disabled={!editable}
                      onChange={(e) =>
                        act(
                          () =>
                            patch(`/admin/schema/columns/${c.id}`, { sensitivity: e.target.value }),
                          () => snap.reload(),
                        )
                      }
                    >
                      {['PUBLIC', 'INTERNAL', 'SENSITIVE', 'RESTRICTED'].map((s) => (
                        <option key={s} value={s}>
                          {s.toLowerCase()}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      defaultChecked={c.is_masked}
                      disabled={!editable}
                      onChange={(e) =>
                        act(
                          () =>
                            patch(`/admin/schema/columns/${c.id}`, { is_masked: e.target.checked }),
                          () => snap.reload(),
                        )
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
      {rel && snap.data && (
        <Modal title="Add relationship" onClose={() => setRel(null)}>
          <div className="adm-form">
            {(['from', 'to'] as const).map((side) => (
              <div key={side} className="rel-side">
                <Select
                  className="select"
                  value={rel[`${side}_table_id`]}
                  onChange={(e) => setRel({ ...rel, [`${side}_table_id`]: e.target.value })}
                >
                  <option value="">{side === 'from' ? 'Child table' : 'Parent table'}</option>
                  {snap.data.tables.map((t: any) => (
                    <option key={t.id} value={t.id}>
                      {t.table_name}
                    </option>
                  ))}
                </Select>
                <input
                  className="input"
                  placeholder="Columns, comma-separated"
                  value={rel[`${side}_columns`]}
                  onChange={(e) => setRel({ ...rel, [`${side}_columns`]: e.target.value })}
                />
              </div>
            ))}
          </div>
          <div className="modal-actions">
            <button className="btn" onClick={() => setRel(null)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={() =>
                act(
                  () =>
                    post(`/admin/schema/snapshots/${sid}/relationships`, {
                      ...rel,
                      from_columns: rel.from_columns.split(',').map((s: string) => s.trim()),
                      to_columns: rel.to_columns.split(',').map((s: string) => s.trim()),
                    }),
                  () => {
                    setRel(null);
                    snap.reload();
                  },
                )
              }
            >
              Add relationship
            </button>
          </div>
        </Modal>
      )}
      {diff && (
        <Modal title="Changes since the last locked version" onClose={() => setDiff(null)}>
          {!diff.baseline ? (
            <p>There's no earlier locked version to compare with.</p>
          ) : (
            <div className="diff-cols">
              <div>
                <h4>Added</h4>
                {diff.added.map((x: string) => (
                  <div key={x} className="mono small">
                    {x}
                  </div>
                ))}
              </div>
              <div>
                <h4>Removed</h4>
                {diff.removed.map((x: string) => (
                  <div key={x} className="mono small">
                    {x}
                  </div>
                ))}
              </div>
              <div>
                <h4>Changed</h4>
                {diff.changed.map((x: string) => (
                  <div key={x} className="mono small">
                    {x}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Modal>
      )}
    </Section>
  );
}
