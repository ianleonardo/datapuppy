import { describe, expect, it } from 'vite-plus/test';
import {
  THEMES,
  pickGreeting,
  themeForHour,
  timeOfDay,
  TIME_GREETINGS,
  type ThemeName,
} from './themes';

// WCAG 2.1 relative luminance / contrast (TR-UI-04)
function lum(hex: string): number {
  const v = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(v.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}

const PAIRS: [string, string, number][] = [
  ['--ink', '--surface', 4.5],
  ['--ink', '--surface-2', 4.5],
  ['--ink-2', '--surface', 4.5],
  ['--ink', '--user-bubble', 4.5],
  ['--ink', '--bot-bubble', 4.5],
  ['--code-ink', '--code-bg', 4.5],
  ['--pill-ok-ink', '--pill-ok-bg', 4.5],
  ['--pill-warn-ink', '--pill-warn-bg', 4.5],
  ['--pill-bad-ink', '--pill-bad-bg', 4.5],
  ['--accent-ink', '--accent-soft', 4.5],
  ['--go-ink', '--go', 3], // large/bold button text
  ['--brand-ink', '--btn-brand', 3],
  ['--brand-ink', '--btn-brand-2', 3],
];

describe('theme tokens meet WCAG AA', () => {
  for (const name of Object.keys(THEMES) as ThemeName[]) {
    for (const [fg, bg, min] of PAIRS) {
      it(`${name}: ${fg} on ${bg} ≥ ${min}`, () => {
        const t = THEMES[name].tokens;
        expect(contrast(t[fg], t[bg])).toBeGreaterThanOrEqual(min);
      });
    }
  }
});

describe('time-of-day theme resolver (DD-10)', () => {
  it('maps hours to windows', () => {
    expect(themeForHour(5)).toBe('morning');
    expect(themeForHour(11)).toBe('morning');
    expect(themeForHour(12)).toBe('afternoon');
    expect(themeForHour(17)).toBe('afternoon');
    expect(themeForHour(18)).toBe('night');
    expect(themeForHour(2)).toBe('night');
  });
  it('each theme has its own brand and mascot', () => {
    expect(new Set(Object.values(THEMES).map((t) => t.brand)).size).toBe(3);
    expect(THEMES.morning.brand).toBe('QueryPup');
    expect(THEMES.afternoon.brand).toBe('Tiangou');
    expect(THEMES.night.brand).toBe('DataBark');
  });
});

describe('chat greeting follows the clock, not the theme', () => {
  it('maps hours to time of day', () => {
    expect([6, 11, 12, 17, 18, 21, 22, 2].map(timeOfDay)).toEqual([
      'morning',
      'morning',
      'afternoon',
      'afternoon',
      'evening',
      'evening',
      'lateNight',
      'lateNight',
    ]);
  });
  it('picks from the bucket for the given time', () => {
    const at = (h: number) => new Date(2026, 0, 1, h, 30);
    expect(TIME_GREETINGS.morning).toContain(pickGreeting(at(8)));
    expect(TIME_GREETINGS.afternoon).toContain(pickGreeting(at(14)));
    expect(TIME_GREETINGS.evening).toContain(pickGreeting(at(20)));
    expect(TIME_GREETINGS.lateNight).toContain(pickGreeting(at(1)));
  });
});
