import { create } from 'zustand';
import type { Mood } from './Mascot';

/**
 * Mood state machine (TS §16.1): changes are debounced to 400 ms; idle > 30 s settles to `idle` (breathing),
 * AFK > 5 min → `sleepy`; activity while sleepy → `waking` → `idle`.
 */
interface MoodState {
  mood: Mood;
  setMood: (m: Mood, holdMs?: number) => void;
  activity: () => void;
}

let debounce: ReturnType<typeof setTimeout> | undefined;
let hold: ReturnType<typeof setTimeout> | undefined;
let sleepTimer: ReturnType<typeof setTimeout> | undefined;

export const useMood = create<MoodState>((set, get) => ({
  mood: 'waking',
  setMood: (m, holdMs) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      clearTimeout(hold);
      set({ mood: m });
      const settle =
        holdMs ?? (m === 'waking' ? 1900 : m === 'thinking' ? 0 : m === 'sleepy' ? 0 : 30_000);
      if (settle > 0) hold = setTimeout(() => set({ mood: 'idle' }), settle);
    }, 400);
  },
  activity: () => {
    clearTimeout(sleepTimer);
    if (get().mood === 'sleepy') get().setMood('waking');
    sleepTimer = setTimeout(() => get().setMood('sleepy'), 5 * 60_000);
  },
}));

export function startActivityTracking(): () => void {
  const fn = () => useMood.getState().activity();
  const evs = ['mousemove', 'keydown', 'pointerdown', 'wheel'];
  let last = 0;
  const throttled = () => {
    const now = Date.now();
    if (now - last > 2000) {
      last = now;
      fn();
    }
  };
  evs.forEach((e) => window.addEventListener(e, throttled, { passive: true }));
  fn();
  return () => evs.forEach((e) => window.removeEventListener(e, throttled));
}
