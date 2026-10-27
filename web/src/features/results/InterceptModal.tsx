import { useEffect, useRef } from 'react';
import { useApp } from '../../app/store';
import { useExec } from '../../lib/exec';
import { Mascot } from '../../mascot/Mascot';

/** Large-payload interception (FS 2.4.1 / TS §12.3) — guardian-mode pup + the exact FS wording. */
export function InterceptModal() {
  const i = useExec((s) => s.interception);
  const theme = useApp((s) => s.me?.theme ?? 'morning');
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (i) okRef.current?.focus();
  }, [i]);
  if (!i) return null;
  const rows =
    i.rows == null
      ? 'an unknown number of'
      : `${i.method === 'EXPLAIN' ? '≈' : ''}${i.rows.toLocaleString()}${i.method === 'EXPLAIN' ? ' (estimated)' : ''}`;
  return (
    <div
      className="modal-back"
      role="dialog"
      aria-modal="true"
      aria-labelledby="intercept-title"
      onKeyDown={(e) => e.key === 'Escape' && i.resolve(false)}
    >
      <div className="modal intercept">
        <div className="intercept-pup">
          <Mascot theme={theme} mood="protective" size={150} />
        </div>
        <div>
          <h3 id="intercept-title">Whoa, hold on!</h3>
          <p className="intercept-friendly">
            That query is going to return {rows} rows. Want me to paginate it so your screen doesn't
            freeze up?
          </p>
          <p className="intercept-formal">
            Warning: The requested query returns {rows} rows and {i.cols} columns. Rendering this
            large payload may impact browser performance. Do you wish to proceed with paginated
            streaming?
          </p>
          <p className="muted small">
            Limits: {i.thresholds?.max_rows?.toLocaleString()} rows or {i.thresholds?.max_cols}{' '}
            columns.
          </p>
          <div className="modal-actions">
            <button className="btn" onClick={() => i.resolve(false)}>
              Cancel
            </button>
            <button ref={okRef} className="btn btn-go" onClick={() => i.resolve(true)}>
              Paginate it
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
