import type { ThemeName } from '../theme/themes';
import { Mascot } from '../mascot/Mascot';

/** Brand wordmark per theme (morning QueryPup · afternoon Tiangou · night DataBark). */
export function Wordmark({ theme, size = 'md' }: { theme: ThemeName; size?: 'md' | 'lg' }) {
  const cls = `wordmark wm-${theme} wm-${size}`;
  if (theme === 'morning')
    return (
      <div className={cls} role="heading" aria-level={1} aria-label="QueryPup">
        <span className="wm-a">Query</span>
        <span className="wm-b">Pup</span>
        <Mascot theme="morning" headOnly size={size === 'lg' ? 46 : 32} mood="idle" title="" />
      </div>
    );
  if (theme === 'afternoon')
    return (
      <div className={cls} role="heading" aria-level={1} aria-label="Tiangou">
        <svg
          className="wm-paw"
          viewBox="-30 -30 60 60"
          width={size === 'lg' ? 46 : 32}
          height={size === 'lg' ? 46 : 32}
          aria-hidden="true"
        >
          <ellipse
            cx="0"
            cy="10"
            rx="16"
            ry="13"
            fill="#FFD23F"
            stroke="#4B2A1B"
            strokeWidth="3.5"
          />
          {[
            [-17, -6],
            [-6, -16],
            [6, -16],
            [17, -6],
          ].map(([x, y], i) => (
            <ellipse
              key={i}
              cx={x}
              cy={y}
              rx="6"
              ry="7.5"
              fill={['#FF5E5B', '#4CC9F0', '#7BD389', '#9B5DE5'][i]}
              stroke="#4B2A1B"
              strokeWidth="3"
            />
          ))}
          <path
            d="M-8 14 v-6 M-2 14 v-10 M4 14 v-7 M10 14 v-4"
            stroke="#4B2A1B"
            strokeWidth="2.6"
            strokeLinecap="round"
          />
        </svg>
        <span className="wm-a">Tiangou</span>
      </div>
    );
  return (
    <div className={cls} role="heading" aria-level={1} aria-label="DataBark">
      <span className="wm-a">DataBark</span>
      <svg
        viewBox="-30 -30 60 60"
        width={size === 'lg' ? 34 : 24}
        height={size === 'lg' ? 34 : 24}
        aria-hidden="true"
        className="wm-paw"
      >
        <ellipse cx="0" cy="10" rx="14" ry="11" fill="currentColor" />
        {[
          [-15, -5],
          [-5, -14],
          [6, -14],
          [16, -5],
        ].map(([x, y], i) => (
          <ellipse key={i} cx={x} cy={y} rx="5" ry="6.5" fill="currentColor" />
        ))}
      </svg>
      <span className="wm-moon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18">
          <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" fill="#FFD36B" />
        </svg>
      </span>
    </div>
  );
}
