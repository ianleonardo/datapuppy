import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { useApp } from '../../app/store';

const CLASS_PILL: Record<string, string> = {
  FACT: 'pill-info',
  DIMENSION: 'pill-ok',
  BACKUP: 'pill-bad',
  TEMP: 'pill-bad',
  ARCHIVE: 'pill-warn',
  STAGING: 'pill-warn',
};

/** Country → Database → Schema → Table tree (TS §5.4). */
export function SchemaPanel() {
  const connectionId = useApp((s) => s.connectionId);
  const setConnectionId = useApp((s) => s.setConnectionId);
  const setErd = useApp((s) => s.setErd);
  const openInEditor = useApp((s) => s.openInEditor);
  const [tree, setTree] = useState<any>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [detail, setDetail] = useState<any>(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    get('/schema/tree')
      .then(setTree)
      .catch(() => setTree({ countries: [] }));
  }, []);
  useEffect(() => {
    if (connectionId) setOpen((o) => ({ ...o, [connectionId]: true }));
  }, [connectionId]);

  async function showTable(dbId: string, t: any) {
    const d = await get(`/schema/${dbId}/tables/${t.id}`);
    setDetail({ ...d, dbId });
  }
  const f = filter.toLowerCase();

  return (
    <div className="schema">
      <input
        className="input schema-filter"
        placeholder="Filter tables"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="schema-body">
        <ul className="tree">
          {tree?.countries.map((c: any) => (
            <li key={c.code}>
              <div className="tree-country">
                {c.name || c.code} <span className="muted">({c.code})</span>
              </div>
              <ul>
                {c.databases.map((d: any) => (
                  <li key={d.id}>
                    <button
                      className={`tree-db ${d.id === connectionId ? 'on' : ''}`}
                      onClick={() => {
                        setOpen((o) => ({ ...o, [d.id]: !o[d.id] }));
                        setConnectionId(d.id);
                      }}
                    >
                      {open[d.id] ? '▾' : '▸'} {d.name}{' '}
                      <span className="pill pill-info">{d.engine.toLowerCase()}</span>
                      {!d.snapshot && <span className="pill pill-warn">not crawled</span>}
                      {d.snapshot?.state === 'DRAFT' && (
                        <span className="pill pill-warn">draft</span>
                      )}
                    </button>
                    {open[d.id] && (
                      <ul>
                        {d.schemas.map((s: any) => (
                          <li key={s.name}>
                            <div className="tree-schema">{s.name}</div>
                            <ul>
                              {s.tables
                                .filter(
                                  (t: any) =>
                                    !f ||
                                    t.name.toLowerCase().includes(f) ||
                                    (t.alias || '').toLowerCase().includes(f),
                                )
                                .map((t: any) => (
                                  <li key={t.id}>
                                    <button
                                      className={`tree-table ${detail?.id === t.id ? 'on' : ''} ${t.inclusion !== 'INCLUDED' ? 'excluded' : ''}`}
                                      onClick={() => showTable(d.id, t)}
                                    >
                                      <span className="tt-name">{t.name}</span>
                                      {t.alias && <span className="muted">“{t.alias}”</span>}
                                      {t.type === 'VIEW' && <span className="pill">view</span>}
                                      {t.classification !== 'UNKNOWN' && (
                                        <span
                                          className={`pill ${CLASS_PILL[t.classification] || ''}`}
                                        >
                                          {t.classification.toLowerCase()}
                                        </span>
                                      )}
                                      {t.inclusion !== 'INCLUDED' && (
                                        <span className="pill pill-bad">
                                          {t.inclusion === 'EXCLUDED' ? 'hidden' : 'pending'}
                                        </span>
                                      )}
                                    </button>
                                  </li>
                                ))}
                            </ul>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
          {tree && tree.countries.length === 0 && (
            <li className="muted small">No databases are shared with you yet.</li>
          )}
        </ul>
        {detail && (
          <aside className="table-detail">
            <div className="td-head">
              <strong className="mono">{detail.table_name}</strong>
              <span className="grow" />
              <button
                className="btn btn-sm"
                onClick={async () => {
                  const r = await get(
                    `/schema/${detail.dbId}/erd?tables=${encodeURIComponent(detail.table_name)}`,
                  );
                  setErd({ source: r.mermaid, title: detail.table_name });
                }}
              >
                Diagram
              </button>
              <button
                className="btn btn-sm"
                onClick={() =>
                  openInEditor(
                    `SELECT *\nFROM ${detail.schema_name}.${detail.table_name}\nLIMIT 50`,
                  )
                }
              >
                Query
              </button>
            </div>
            {detail.description && <p className="small">{detail.description}</p>}
            <table className="cols">
              <tbody>
                {detail.columns.map((c: any) => (
                  <tr key={c.id}>
                    <td className="mono">
                      {c.is_pk ? '🔑 ' : ''}
                      {c.column_name}
                    </td>
                    <td className="muted mono small">{c.data_type}</td>
                    <td>
                      {c.sensitivity === 'RESTRICTED' || c.sensitivity === 'SENSITIVE' ? (
                        <span className="pill pill-warn">{c.sensitivity.toLowerCase()}</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </aside>
        )}
      </div>
    </div>
  );
}
