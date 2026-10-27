import * as Select from '@radix-ui/react-select';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, post, waitJob } from '../../api/client';
import { useApp } from '../../app/store';
import { loadPage } from '../../lib/exec';
import { fmtCell } from '../../lib/sql';
import { copyText, toast, toastError } from '../../lib/toast';
import { Mascot } from '../../mascot/Mascot';
import { THEMES } from '../../theme/themes';

type Sort = { col: string; dir: 'asc' | 'desc' } | null;

export function GridPanel() {
  const me = useApp((s) => s.me)!;
  const r = useApp((s) => s.result);
  const busy = useApp((s) => s.resultBusy);
  const [sort, setSort] = useState<Sort>(null);
  const [pageInput, setPageInput] = useState('1');
  const [exporting, setExporting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const options = me.ui.page_size_options || [25, 50, 100, 200];
  const mascotName = THEMES[me.theme].mascot;

  useEffect(() => setPageInput(String(r?.page ?? 1)), [r?.page]);
  useEffect(() => setSort(null), [r?.run_id]);

  const columns = useMemo<ColumnDef<any[]>[]>(
    () =>
      (r?.columns || []).map((c, i) => ({
        id: c.name,
        header: c.name,
        accessorFn: (row) => row[i],
        meta: { type: c.type },
      })),
    [r?.columns],
  );
  const table = useReactTable({
    data: r?.rows || [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
  });
  const rows = table.getRowModel().rows;
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 40,
    overscan: 12,
  });

  if (!r)
    return (
      <div className="grid-empty">
        <Mascot theme={me.theme} mood="idle" size={110} />
        <p>Run a query from the chat or the SQL editor and the rows will land here.</p>
      </div>
    );

  const sortStr = (s: Sort) => (s ? `${s.col}:${s.dir}` : undefined);
  const go = (page: number, size = r.page_size, s: Sort = sort) => {
    const last = r.total_pages ?? Infinity;
    const p = Math.max(1, Math.min(page, last));
    loadPage(p, size, sortStr(s));
  };
  const toggleSort = (col: string) => {
    const next: Sort =
      !sort || sort.col !== col
        ? { col, dir: 'asc' }
        : sort.dir === 'asc'
          ? { col, dir: 'desc' }
          : null;
    setSort(next);
    go(1, r.page_size, next);
  };
  async function exportXlsx() {
    setExporting(true);
    try {
      const res = await post(`/results/${r!.run_id}/export`);
      let url = res.download_url;
      if (!url && res.job_id) {
        toast('Building a large export in the background…');
        const j = await waitJob(res.job_id);
        if (j.status !== 'SUCCEEDED') throw new Error(j.error || 'Export failed.');
        url = j.result.download_url;
      }
      window.location.href = url;
      toast('Export ready');
    } catch (e) {
      toastError((e as ApiError).message);
    } finally {
      setExporting(false);
    }
  }
  async function rerun() {
    try {
      const res = await post(`/results/${r!.run_id}/rerun`);
      useApp.getState().setResult({ ...res, sql: r!.sql, connection_id: r!.connection_id });
    } catch (e) {
      toastError((e as ApiError).message);
    }
  }

  const totalTxt =
    r.total_rows == null
      ? 'an unknown number of'
      : `${r.row_count_method === 'EXPLAIN' ? '≈' : ''}${r.total_rows.toLocaleString()}`;
  const items = virt.getVirtualItems();
  const padTop = items.length ? items[0].start : 0;
  const padBottom = items.length ? virt.getTotalSize() - items[items.length - 1].end : 0;

  return (
    <div className={`grid ${busy ? 'is-busy' : ''}`}>
      <div className="grid-banner" role="status">
        <svg
          viewBox="-14 -16 28 28"
          width="20"
          height="20"
          aria-hidden="true"
          className="banner-paw"
        >
          <ellipse cx="0" cy="5" rx="7" ry="6" fill="currentColor" />
          <ellipse cx="-7" cy="-4" rx="2.8" ry="3.6" fill="currentColor" />
          <ellipse cx="-2.4" cy="-8" rx="2.8" ry="3.6" fill="currentColor" />
          <ellipse cx="3" cy="-8" rx="2.8" ry="3.6" fill="currentColor" />
          <ellipse cx="7.6" cy="-4" rx="2.8" ry="3.6" fill="currentColor" />
        </svg>
        <span>
          <strong>{mascotName} is on it:</strong> pre-flight check passed! {totalTxt} records found
          (streaming {r.page_size} per page).
        </span>
        <span className="grow" />
        <span
          className="live-note"
          title="Pages are re-read from the source database, so rows can change between pages."
        >
          Live data
        </span>
        {r.sql && (
          <button className="btn btn-sm btn-ghost" onClick={() => copyText(r.sql!, 'SQL copied')}>
            Copy SQL
          </button>
        )}
      </div>
      <div className="grid-scroll" ref={scrollRef}>
        <table className="grid-table">
          <thead>
            <tr>
              <th className="rownum">#</th>
              {table.getHeaderGroups()[0]?.headers.map((h) => {
                const active = sort?.col === h.id;
                return (
                  <th
                    key={h.id}
                    aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      className="th-btn"
                      onClick={() => toggleSort(h.id)}
                      title={`${(h.column.columnDef.meta as any)?.type || ''} · click to sort`}
                    >
                      {h.id}
                      <span className="sort-ico">
                        {active ? (sort!.dir === 'asc' ? '▲' : '▼') : ''}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {padTop > 0 && (
              <tr style={{ height: padTop }}>
                <td colSpan={columns.length + 1} />
              </tr>
            )}
            {items.map((vi) => {
              const row = rows[vi.index];
              return (
                <tr key={row.id}>
                  <td className="rownum">{(r.page - 1) * r.page_size + vi.index + 1}</td>
                  {row.getVisibleCells().map((cell) => {
                    const f = fmtCell(
                      cell.getValue(),
                      (cell.column.columnDef.meta as any)?.type || '',
                      cell.column.id,
                    );
                    return (
                      <td
                        key={cell.id}
                        className={f.align === 'right' ? 'num' : ''}
                        onDoubleClick={() => copyText(f.text, 'Cell copied')}
                      >
                        {f.cls ? <span className={f.cls}>{f.text}</span> : f.text}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {padBottom > 0 && (
              <tr style={{ height: padBottom }}>
                <td colSpan={columns.length + 1} />
              </tr>
            )}
          </tbody>
        </table>
        {r.rows.length === 0 && <p className="grid-none">No rows match this query.</p>}
      </div>
      <div className="grid-foot">
        <button
          className="icon-btn"
          onClick={() => go(1)}
          disabled={r.page <= 1}
          aria-label="First page"
        >
          ⏮
        </button>
        <button
          className="icon-btn"
          onClick={() => go(r.page - 1)}
          disabled={r.page <= 1}
          aria-label="Previous page"
        >
          ‹
        </button>
        <span>Page</span>
        <input
          className="input page-input"
          value={pageInput}
          onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ''))}
          onKeyDown={(e) => e.key === 'Enter' && go(Number(pageInput) || 1)}
          onBlur={() => Number(pageInput) !== r.page && go(Number(pageInput) || 1)}
          aria-label="Page number"
        />
        <span>of {r.total_pages ?? '?'}</span>
        <button
          className="icon-btn"
          onClick={() => go(r.page + 1)}
          disabled={r.total_pages != null ? r.page >= r.total_pages : !(r as any).has_more}
          aria-label="Next page"
        >
          ›
        </button>
        <button
          className="icon-btn"
          onClick={() => go(r.total_pages ?? r.page)}
          disabled={r.total_pages == null || r.page >= r.total_pages}
          aria-label="Last page"
        >
          ⏭
        </button>
        <span className="grow" />
        <Select.Root value={String(r.page_size)} onValueChange={(v) => go(1, Number(v))}>
          <Select.Trigger className="select-trigger" aria-label="Rows per page">
            <Select.Value /> <Select.Icon>▾</Select.Icon>
          </Select.Trigger>
          <Select.Portal>
            <Select.Content className="dd-content" position="popper" sideOffset={6}>
              <Select.Viewport>
                {options.map((o) => (
                  <Select.Item key={o} value={String(o)} className="dd-item">
                    <Select.ItemText>{o} rows</Select.ItemText>
                  </Select.Item>
                ))}
              </Select.Viewport>
            </Select.Content>
          </Select.Portal>
        </Select.Root>
        <button className="btn" onClick={rerun} title="Run the query again">
          Re-run
        </button>
        <button className="btn btn-export" onClick={exportXlsx} disabled={exporting}>
          {exporting ? 'Exporting…' : 'Export to Excel'}
        </button>
      </div>
    </div>
  );
}
