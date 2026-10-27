import type { IDockviewHeaderActionsProps } from 'dockview-react';
import { useEffect, useState } from 'react';

/**
 * Buttons on the right of every panel group's tab bar:
 *  - Float / Dock: pop the group out as a floating window over the workspace, or dock it back.
 *  - Maximize / Restore: fill the whole workspace with this group (Esc or the button restores).
 */
export function GroupActions({ api, containerApi, group }: IDockviewHeaderActionsProps) {
  const [, tick] = useState(0);
  useEffect(() => {
    const subs = [
      containerApi.onDidMaximizedGroupChange(() => tick((n) => n + 1)),
      api.onDidLocationChange(() => tick((n) => n + 1)),
    ];
    return () => subs.forEach((s) => s.dispose());
  }, [api, containerApi]);

  const floating = api.location.type === 'floating';
  const maximized = !floating && api.isMaximized();
  const anyMaximized = containerApi.hasMaximizedGroup();

  return (
    <div className="grp-actions">
      {!maximized && (
        <button
          className="grp-btn"
          title={floating ? 'Dock back into the workspace' : 'Float as a separate window'}
          aria-label={floating ? 'Dock panel' : 'Float panel'}
          onClick={() => {
            if (floating) api.moveTo({ position: 'right' });
            else {
              if (anyMaximized) containerApi.exitMaximizedGroup();
              const w = containerApi.width;
              const h = containerApi.height;
              containerApi.addFloatingGroup(group, {
                position: { left: Math.round(w * 0.2), top: Math.round(h * 0.12) },
                width: Math.round(w * 0.6),
                height: Math.round(h * 0.7),
              });
            }
          }}
        >
          {floating ? <IconDock /> : <IconFloat />}
        </button>
      )}
      {!floating && (
        <button
          className="grp-btn"
          title={maximized ? 'Restore (Esc)' : 'Maximize (or double-click a tab)'}
          aria-label={maximized ? 'Restore panel size' : 'Maximize panel'}
          aria-pressed={maximized}
          onClick={() => (maximized ? api.exitMaximized() : api.maximize())}
        >
          {maximized ? <IconRestore /> : <IconMax />}
        </button>
      )}
    </div>
  );
}

const svg = {
  width: 15,
  height: 15,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};
const IconMax = () => (
  <svg {...svg}>
    <path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" />
  </svg>
);
const IconRestore = () => (
  <svg {...svg}>
    <path d="M13.5 6.5h-4v-4M2.5 9.5h4v4M9.5 6.5l4-4M6.5 9.5l-4 4" />
  </svg>
);
const IconFloat = () => (
  <svg {...svg}>
    <rect x="2" y="5" width="9" height="9" rx="1.5" />
    <path d="M6 2.5h6a1.5 1.5 0 0 1 1.5 1.5v6" />
  </svg>
);
const IconDock = () => (
  <svg {...svg}>
    <rect x="2" y="2.5" width="12" height="11" rx="1.5" />
    <path d="M9 2.5v11" />
  </svg>
);
