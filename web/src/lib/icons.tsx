import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

/** Small line icons (16 px grid, currentColor) used for compact tool buttons. */
const base = {
  width: 17,
  height: 17,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
  focusable: false,
};

export const Icon = {
  copy: () => (
    <svg {...base}>
      <rect x="5.5" y="5.5" width="8" height="8.5" rx="1.6" />
      <path d="M10.5 5.5V3.6A1.6 1.6 0 0 0 8.9 2H3.6A1.6 1.6 0 0 0 2 3.6v5.3a1.6 1.6 0 0 0 1.6 1.6h1.9" />
    </svg>
  ),
  check: () => (
    <svg {...base}>
      <path d="m3.5 8.4 2.9 2.9 6.1-6.6" />
    </svg>
  ),
  speaker: () => (
    <svg {...base}>
      <path d="M2.5 6.2h2.2L8 3.3v9.4L4.7 9.8H2.5z" />
      <path d="M10.6 5.6a3.3 3.3 0 0 1 0 4.8M12.4 3.9a5.7 5.7 0 0 1 0 8.2" />
    </svg>
  ),
  stop: () => (
    <svg {...base}>
      <rect x="4" y="4" width="8" height="8" rx="1.4" fill="currentColor" stroke="none" />
    </svg>
  ),
  thumbUp: () => (
    <svg {...base}>
      <path d="M5 7.2v6.3M5 7.2 7.6 2.5c.9 0 1.6.8 1.4 1.7l-.4 2.3h3.6c.9 0 1.6.9 1.4 1.8l-.9 4.1a1.6 1.6 0 0 1-1.6 1.1H5M5 7.2H3.1a.6.6 0 0 0-.6.6v5.1c0 .3.3.6.6.6H5" />
    </svg>
  ),
  thumbDown: () => (
    <svg {...base}>
      <path d="M11 8.8V2.5M11 8.8l-2.6 4.7c-.9 0-1.6-.8-1.4-1.7l.4-2.3H3.8c-.9 0-1.6-.9-1.4-1.8l.9-4.1a1.6 1.6 0 0 1 1.6-1.1H11m0 6.3h1.9c.3 0 .6-.3.6-.6V3.1a.6.6 0 0 0-.6-.6H11" />
    </svg>
  ),
  newFile: () => (
    <svg {...base}>
      <path d="M9 2H4.6A1.6 1.6 0 0 0 3 3.6v8.8A1.6 1.6 0 0 0 4.6 14h6.8a1.6 1.6 0 0 0 1.6-1.6V6z" />
      <path d="M9 2v4h4M8 8.5v3.5M6.25 10.25h3.5" />
    </svg>
  ),
  history: () => (
    <svg {...base}>
      <path d="M2.6 8a5.4 5.4 0 1 0 1.6-3.8" />
      <path d="M2.4 2.6v2.6H5M8 5v3.2l2.1 1.4" />
    </svg>
  ),
  checkCircle: () => (
    <svg {...base}>
      <circle cx="8" cy="8" r="5.9" />
      <path d="m5.4 8.2 1.8 1.8 3.5-3.8" />
    </svg>
  ),
  sparkles: () => (
    <svg {...base}>
      <path d="M6.5 2.5 7.6 5.8l3.3 1.1-3.3 1.1-1.1 3.3-1.1-3.3-3.3-1.1 3.3-1.1zM12 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
    </svg>
  ),
  format: () => (
    <svg {...base}>
      <path d="M2.5 3.5h11M5.5 6.5h8M5.5 9.5h8M2.5 12.5h11" />
    </svg>
  ),
  spinner: () => (
    <svg {...base} className="ico-spin">
      <path d="M8 2.2a5.8 5.8 0 1 1-5.8 5.8" />
    </svg>
  ),
};

type IconButtonProps = {
  label: string;
  icon: ReactNode;
  tipSide?: 'below' | 'above';
} & ButtonHTMLAttributes<HTMLButtonElement>;

/** Icon-only button with an accessible name and a hover/focus tooltip (the label). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, className = '', tipSide = 'below', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      data-tip={label}
      data-tip-side={tipSide}
      className={`ibtn ${className}`}
      {...rest}
    >
      {icon}
    </button>
  );
});
