import { Fragment } from 'react';

const KW = new Set(
  (
    'select from where and or not in is null as join left right inner outer full cross on group by order having limit offset fetch first next rows ' +
    'only with union all distinct case when then else end between like ilike exists over partition desc asc lag lead count sum avg min max ' +
    'coalesce nvl cast date_trunc interval true false row_number rank dense_rank using natural lateral filter window intersect except'
  ).split(' '),
);
const TOKEN =
  /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)(\s*\()?|(\s+)|(.)/g;

/** Lightweight SQL highlighter for chat cards (Monaco is used in the editor panel). */
export function HighlightedSql({ sql }: { sql: string }) {
  const out: JSX.Element[] = [];
  let m: RegExpExecArray | null;
  let i = 0;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(sql))) {
    const [, comment, str, num, word, paren, ws, other] = m;
    if (comment)
      out.push(
        <span key={i++} className="tk-cm">
          {comment}
        </span>,
      );
    else if (str)
      out.push(
        <span key={i++} className="tk-str">
          {str}
        </span>,
      );
    else if (num)
      out.push(
        <span key={i++} className="tk-num">
          {num}
        </span>,
      );
    else if (word) {
      const lw = word.toLowerCase();
      if (KW.has(lw) && !paren)
        out.push(
          <span key={i++} className="tk-kw">
            {word}
          </span>,
        );
      else if (paren)
        out.push(
          <Fragment key={i++}>
            <span className={KW.has(lw) ? 'tk-kw' : 'tk-fn'}>{word}</span>
            {paren}
          </Fragment>,
        );
      else out.push(<Fragment key={i++}>{word}</Fragment>);
    } else out.push(<Fragment key={i++}>{ws ?? other}</Fragment>);
  }
  return <>{out}</>;
}

export const STATUS_OK =
  /^(complete[d]?|success(ful)?|settled|ok|active|paid|approved|done|yes|true)$/i;
export const STATUS_WARN =
  /^(pending|waiting|processing|in[ _]progress|queued|partial|hold|review)$/i;
export const STATUS_BAD =
  /^(cancel+ed|failed|failure|refunded|error|rejected|declined|inactive|no|false|overdue)$/i;

const MEASURE = /(amount|amt|total|sum|revenue|price|cost|balance|qty|quantity|count|value|sales)/i;

export function fmtCell(
  v: any,
  type: string,
  col = '',
): { text: string; cls?: string; align?: 'right' } {
  if (v === null || v === undefined) return { text: 'NULL', cls: 'cell-null' };
  if (typeof v === 'number') {
    const t = type.toUpperCase();
    if (Number.isInteger(v) && !/DEC|NUMERIC|MONEY|DOUBLE|FLOAT|REAL/.test(t) && !MEASURE.test(col))
      return { text: String(v), align: 'right' };
    const isMoney = /DEC|NUMERIC|MONEY/.test(t) || (MEASURE.test(col) && !Number.isInteger(v));
    return {
      text: isMoney
        ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : v.toLocaleString(),
      align: 'right',
    };
  }
  if (typeof v === 'boolean') return { text: String(v), cls: v ? 'pill pill-ok' : 'pill pill-bad' };
  const s = String(v);
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s))
    return {
      text: s
        .replace('T', ' ')
        .replace(/(:\d{2})(\.\d+)?([+-]\d{2}:\d{2}|Z)?$/, '$1')
        .slice(0, 19),
    };
  if (s.length <= 24) {
    if (STATUS_OK.test(s)) return { text: s, cls: 'pill pill-ok' };
    if (STATUS_WARN.test(s)) return { text: s, cls: 'pill pill-warn' };
    if (STATUS_BAD.test(s)) return { text: s, cls: 'pill pill-bad' };
  }
  return { text: s };
}
