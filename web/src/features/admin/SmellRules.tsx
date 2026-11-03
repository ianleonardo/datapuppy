import { useEffect, useState } from 'react';
import { post, put } from '../../api/client';
import { Select } from '../../lib/Select';
import { toast } from '../../lib/toast';
import { act, Card, Field, Modal, useLoad } from './kit';

type Rule = {
  id: string;
  label: string;
  pattern: string;
  cls: string;
  sev: string;
  enabled?: boolean;
  check_date?: boolean;
};

const SEV_PILL: Record<string, string> = {
  HIGH: 'pill-bad',
  MEDIUM: 'pill-warn',
  LOW: 'pill-info',
};
const SEV_TEXT: Record<string, string> = {
  HIGH: 'excluded',
  MEDIUM: 'needs review',
  LOW: 'flag only',
};

/** Admin-defined regex rules that flag "smelly" tables (backups, temp, archive …) during a crawl. */
export function SmellRules({
  draftId,
  onReapplied,
}: {
  draftId?: string;
  onReapplied?: () => void;
}) {
  const data = useLoad<any>('/admin/schema/smell-rules');
  const [rules, setRules] = useState<Rule[]>([]);
  const [dirty, setDirty] = useState(false);
  const [edit, setEdit] = useState<{ index: number; rule: Rule } | null>(null);
  const [names, setNames] = useState('');
  const [tested, setTested] = useState<any[] | null>(null);

  useEffect(() => {
    if (data.data) {
      setRules(data.data.items);
      setDirty(false);
    }
  }, [data.data]);

  const change = (next: Rule[]) => {
    setRules(next);
    setDirty(true);
    setTested(null);
  };
  const save = () =>
    act(
      () => put('/admin/schema/smell-rules', { items: rules }),
      () => {
        toast('Smell rules saved. They apply to the next crawl, or re-apply them to the draft.');
        data.reload();
      },
    );
  const test = () =>
    act(
      () => post('/admin/schema/smell-rules/test', { items: rules, names }),
      (r: any) => setTested(r.items),
    );
  const reapply = () =>
    draftId &&
    act(
      async () => {
        if (dirty) await put('/admin/schema/smell-rules', { items: rules });
        return post(`/admin/schema/snapshots/${draftId}/reapply-smells`);
      },
      (r: any) => {
        toast(`Re-applied: ${r.flagged} flagged, ${r.excluded} excluded, ${r.pending} to review`);
        data.reload();
        onReapplied?.();
      },
    );

  if (!data.data) return null;
  const classes: string[] = data.data.classes;
  const severities: string[] = data.data.severities;
  const nextId = () => {
    let n = rules.length + 1;
    while (rules.some((r) => r.id === `SM-${String(n).padStart(2, '0')}`)) n++;
    return `SM-${String(n).padStart(2, '0')}`;
  };

  return (
    <Card>
      <div className="smell-head">
        <h4>Smell rules</h4>
        <span className="grow" />
        <button
          className="btn btn-sm"
          onClick={() =>
            setEdit({
              index: -1,
              rule: {
                id: nextId(),
                label: '',
                pattern: '',
                cls: 'BACKUP',
                sev: 'MEDIUM',
                enabled: true,
              },
            })
          }
        >
          Add rule
        </button>
        <button
          className="btn btn-sm"
          onClick={() => {
            if (confirm('Replace all rules with the built-in defaults?'))
              change(data.data.defaults);
          }}
        >
          Reset to defaults
        </button>
        {draftId && (
          <button
            className="btn btn-sm"
            onClick={reapply}
            title="Re-check every table of the draft"
          >
            Re-apply to draft
          </button>
        )}
        <button className="btn btn-sm btn-primary" onClick={save} disabled={!dirty}>
          Save rules
        </button>
      </div>
      <p className="small muted">
        A table whose name matches a regex (case-insensitive) is flagged. HIGH: excluded
        automatically; MEDIUM: waits for review before lock; LOW: flagged only. Your own
        include/exclude decisions are never overwritten.
      </p>
      <div className="smell-table-wrap">
        <table className="adm-table smell-table">
          <thead>
            <tr>
              <th>On</th>
              <th>ID</th>
              <th>Description</th>
              <th>Regex</th>
              <th>Class</th>
              <th>Severity</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.map((r, i) => (
              <tr key={r.id + i} className={r.enabled === false ? 'off' : ''}>
                <td>
                  <input
                    type="checkbox"
                    checked={r.enabled !== false}
                    aria-label={`Enable ${r.id}`}
                    onChange={(e) =>
                      change(
                        rules.map((x, j) => (j === i ? { ...x, enabled: e.target.checked } : x)),
                      )
                    }
                  />
                </td>
                <td className="small">{r.id}</td>
                <td>{r.label || <span className="muted">—</span>}</td>
                <td>
                  <code className="smell-re">{r.pattern}</code>
                </td>
                <td className="small">{r.cls.toLowerCase()}</td>
                <td>
                  <span className={`pill ${SEV_PILL[r.sev] || ''}`} title={SEV_TEXT[r.sev]}>
                    {r.sev.toLowerCase()}
                  </span>
                </td>
                <td className="smell-actions">
                  <button
                    className="btn btn-sm"
                    onClick={() => setEdit({ index: i, rule: { ...r } })}
                  >
                    Edit
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => {
                      if (confirm(`Delete rule ${r.id}?`)) change(rules.filter((_, j) => j !== i));
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {!rules.length && (
              <tr>
                <td colSpan={7} className="muted">
                  No rules: nothing will be flagged.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="smell-test">
        <textarea
          className="input"
          rows={2}
          placeholder="Try table names (one per line or comma-separated), e.g. ORDERS_BAK, TMP_LOAD, SALES_20250131"
          value={names}
          onChange={(e) => setNames(e.target.value)}
        />
        <button className="btn btn-sm" onClick={test} disabled={!names.trim()}>
          Test names
        </button>
      </div>
      {tested && (
        <ul className="ldap-steps">
          {tested.map((t) => (
            <li key={t.name}>
              <code>{t.name}</code>{' '}
              {t.flags.length ? (
                <>
                  <span className={`pill ${SEV_PILL[t.severity] || ''}`}>
                    {SEV_TEXT[t.severity] || t.result.toLowerCase()}
                  </span>{' '}
                  <span className="muted small">
                    {t.flags.join(', ')} → {String(t.classification).toLowerCase()}
                  </span>
                </>
              ) : (
                <span className="pill pill-ok">clean</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {edit && (
        <Modal
          title={edit.index < 0 ? 'Add smell rule' : `Edit ${edit.rule.id}`}
          onClose={() => setEdit(null)}
        >
          <RuleForm
            rule={edit.rule}
            classes={classes}
            severities={severities}
            onCancel={() => setEdit(null)}
            onSave={(r) => {
              change(
                edit.index < 0 ? [...rules, r] : rules.map((x, j) => (j === edit.index ? r : x)),
              );
              setEdit(null);
            }}
          />
        </Modal>
      )}
    </Card>
  );
}

function RuleForm({
  rule,
  classes,
  severities,
  onSave,
  onCancel,
}: {
  rule: Rule;
  classes: string[];
  severities: string[];
  onSave: (r: Rule) => void;
  onCancel: () => void;
}) {
  const [r, setR] = useState<Rule>(rule);
  const [sample, setSample] = useState('');
  let reError = '';
  let matches: boolean | null = null;
  try {
    // JavaScript and Python regex agree for the usual table-name patterns; the server re-checks on save.
    const re = new RegExp(r.pattern, 'i');
    matches = sample ? re.test(sample) : null;
  } catch (e: any) {
    reError = e.message;
  }
  return (
    <div className="adm-form">
      <Field label="ID">
        <input
          className="input"
          value={r.id}
          onChange={(e) => setR({ ...r, id: e.target.value })}
        />
      </Field>
      <Field label="Description">
        <input
          className="input"
          value={r.label}
          placeholder="e.g. Backup copy"
          onChange={(e) => setR({ ...r, label: e.target.value })}
        />
      </Field>
      <Field
        label="Regex (matched against the table name, case-insensitive)"
        hint={reError || undefined}
        wide
      >
        <input
          className="input mono"
          value={r.pattern}
          placeholder="e.g. (_|^)(BAK|BACKUP)(_|$)"
          onChange={(e) => setR({ ...r, pattern: e.target.value })}
        />
      </Field>
      <Field
        label="Try a table name"
        hint={matches === null ? undefined : matches ? 'Matches' : 'No match'}
        wide
      >
        <input className="input" value={sample} onChange={(e) => setSample(e.target.value)} />
      </Field>
      <Field label="Classification">
        <Select
          className="select"
          value={r.cls}
          onChange={(e) => setR({ ...r, cls: e.target.value })}
        >
          {classes.map((c) => (
            <option key={c} value={c}>
              {c.toLowerCase()}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Severity">
        <Select
          className="select"
          value={r.sev}
          onChange={(e) => setR({ ...r, sev: e.target.value })}
        >
          {severities.map((s) => (
            <option key={s} value={s}>
              {s.toLowerCase()} ({SEV_TEXT[s]})
            </option>
          ))}
        </Select>
      </Field>
      <div className="modal-actions">
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          disabled={!r.pattern.trim() || !!reError}
          onClick={() => onSave({ ...r, id: r.id.trim() || 'SM', pattern: r.pattern.trim() })}
        >
          Done
        </button>
      </div>
    </div>
  );
}
