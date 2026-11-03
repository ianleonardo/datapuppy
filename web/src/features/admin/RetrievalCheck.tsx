import { useEffect, useState } from 'react';
import { post } from '../../api/client';
import { act, Card } from './kit';

const KEY = 'sqa.retrievalCheck';

/**
 * Admin test: for typical questions, do the expected tables reach the AI with their full columns?
 * One line per question: "question | TABLE_A, TABLE_B". No AI call is made.
 */
export function RetrievalCheck({ cid }: { cid: string }) {
  const [text, setText] = useState('');
  const [res, setRes] = useState<any>(null);

  useEffect(() => {
    try {
      setText(localStorage.getItem(`${KEY}.${cid}`) || '');
    } catch {
      setText('');
    }
    setRes(null);
  }, [cid]);

  const run = () => {
    try {
      localStorage.setItem(`${KEY}.${cid}`, text);
    } catch {
      /* private mode: just don't remember */
    }
    const items = text
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const [q, t = ''] = l.split('|');
        return {
          question: q.trim(),
          expected: t
            .split(',')
            .map((x) => x.trim())
            .filter(Boolean),
        };
      });
    return act(() => post(`/admin/schema/${cid}/retrieval-check`, { items }), setRes);
  };

  return (
    <Card>
      <h4>Retrieval check</h4>
      <p className="small muted">
        Do your typical questions reach the right tables? One line per question, then <code>|</code>{' '}
        and the tables it should use. The AI always sees every table name; this checks which tables
        it also gets with full columns (the top {res?.full_detail_limit ?? 20}). Uses the locked
        schema; no AI call is made.
      </p>
      <textarea
        className="input mono"
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={
          'top 10 SMS senders last month | PIB.TPIB_SMS_ALERT\nfailed logins per user | TPIB_LOGIN_AUDIT, TPIB_USER'
        }
      />
      <div className="adm-inline">
        <button className="btn btn-sm" onClick={run} disabled={!text.trim()}>
          Check
        </button>
        {res && res.recall !== null && (
          <span
            className={`pill ${res.recall >= 0.95 ? 'pill-ok' : res.recall >= 0.8 ? 'pill-warn' : 'pill-bad'}`}
          >
            {Math.round(res.recall * 100)}% of expected tables reach the AI in full
          </span>
        )}
      </div>
      {res && (
        <table className="adm-table rc-table">
          <thead>
            <tr>
              <th>Question</th>
              <th>Expected tables (rank)</th>
              <th>Top tables found</th>
            </tr>
          </thead>
          <tbody>
            {res.items.map((it: any, i: number) => (
              <tr key={i}>
                <td>{it.question}</td>
                <td>
                  {it.expected.map((e: any) => (
                    <span
                      key={e.table}
                      className={`pill ${!e.exists ? 'pill-bad' : e.in_full ? 'pill-ok' : 'pill-warn'}`}
                      title={
                        !e.exists
                          ? 'Not in the locked schema (excluded, misspelt, or not crawled)'
                          : e.in_full
                            ? 'Sent with full columns'
                            : 'Only the name is sent; the AI must ask for its columns'
                      }
                    >
                      {e.table} {e.exists ? `#${e.rank ?? '–'}` : '(not found)'}
                    </span>
                  ))}
                </td>
                <td className="small muted">{it.top.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
