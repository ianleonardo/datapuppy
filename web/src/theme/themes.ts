// Time-of-day themes (TS §17, FS 3.1). Each theme = brand + mascot + token set.
export type ThemeName = 'morning' | 'afternoon' | 'night';

export interface ThemeDef {
  name: ThemeName;
  brand: string;
  mascot: string;
  label: string;
  greeting: string[];
  /** CSS custom properties (mapped into Tailwind, Monaco, Mermaid, dockview). */
  tokens: Record<string, string>;
  monaco: {
    base: 'vs' | 'vs-dark';
    colors: Record<string, string>;
    rules: { token: string; foreground: string; fontStyle?: string }[];
  };
  mermaid: Record<string, string>;
}

export const THEMES: Record<ThemeName, ThemeDef> = {
  morning: {
    name: 'morning',
    brand: 'QueryPup',
    mascot: 'Pup',
    label: 'Morning',
    greeting: [
      "Morning! Got your Coffee O ready? Let's dig into today's data.",
      "Good morning! I'm awake, stretched, and ready to fetch some schemas whenever you are.",
      'Morning! What are we tracking down today?',
    ],
    tokens: {
      '--bg': '#FFF1DE',
      '--bg-2': '#FFE6C7',
      '--surface': '#FFFDF8',
      '--surface-2': '#FFF6EA',
      '--surface-3': '#FFEBD2',
      '--ink': '#2B1D12',
      '--ink-2': '#6B5444',
      '--ink-3': '#9A8676',
      '--line': '#F7B969',
      '--line-soft': '#F3DEC4',
      '--brand': '#F28C28',
      '--brand-2': '#FFB054',
      '--brand-ink': '#FFFFFF',
      '--btn-brand': '#DD6F12',
      '--btn-brand-2': '#D4680E',
      '--accent': '#8ECDF7',
      '--accent-soft': '#DDF0FD',
      '--accent-ink': '#1E4F78',
      '--coral': '#FF7A6B',
      '--go': '#239A4E',
      '--go-ink': '#FFFFFF',
      '--warn': '#F2A93B',
      '--danger': '#E5484D',
      '--user-bubble': '#D6ECFD',
      '--user-bubble-line': '#7FBDEB',
      '--bot-bubble': '#FFF2DE',
      '--bot-bubble-line': '#F7C27D',
      '--code-bg': '#F4F6F9',
      '--code-ink': '#2B3440',
      '--kw': '#2F6FC2',
      '--str': '#C9433A',
      '--num': '#C9433A',
      '--fn': '#8A4FC1',
      '--radius': '18px',
      '--radius-sm': '12px',
      '--border-w': '2px',
      '--shadow': '0 2px 0 rgba(242,140,40,.18), 0 10px 24px -14px rgba(120,70,20,.35)',
      '--glow': 'none',
      '--frame': 'linear-gradient(180deg,#FFF3E2,#FFE9CF)',
      '--font-display': "'Fredoka', 'Nunito', system-ui, sans-serif",
      '--font-body': "'Nunito', system-ui, sans-serif",
      '--submit-shape': '999px',
      '--focus': '#2F6FC2',
      '--pill-ok-bg': '#CBEFD6',
      '--pill-ok-ink': '#17703A',
      '--pill-warn-bg': '#FFE7A8',
      '--pill-warn-ink': '#7A5200',
      '--pill-bad-bg': '#FFD5D0',
      '--pill-bad-ink': '#A0261C',
      '--color-scheme': 'light',
    },
    monaco: {
      base: 'vs',
      colors: {
        'editor.background': '#FFFDF8',
        'editorLineNumber.foreground': '#C9B39C',
        'editor.lineHighlightBackground': '#FFF3E2',
      },
      rules: [
        { token: 'keyword', foreground: '2F6FC2', fontStyle: 'bold' },
        { token: 'string', foreground: 'C9433A' },
        { token: 'number', foreground: 'C9433A' },
        { token: 'comment', foreground: '9A8676', fontStyle: 'italic' },
      ],
    },
    mermaid: {
      primaryColor: '#E3F2FE',
      primaryBorderColor: '#7FBDEB',
      primaryTextColor: '#2B1D12',
      lineColor: '#F28C28',
      secondaryColor: '#FFF2DE',
      tertiaryColor: '#FFFDF8',
      attributeBackgroundColorOdd: '#FFFDF8',
      attributeBackgroundColorEven: '#FFF3E2',
      rowOdd: '#FFFDF8',
      rowEven: '#FFF3E2',
      fontFamily: 'Nunito, sans-serif',
    },
  },
  afternoon: {
    name: 'afternoon',
    brand: 'Tiangou',
    mascot: 'Tiangou',
    label: 'Afternoon',
    greeting: [
      'Good afternoon! How are those queries coming along? Let me know if you need me to sniff out any specific tables.',
      'Afternoon! Ready to jump back into the data?',
      "Hey there! Let's keep the momentum going. What should we query next?",
    ],
    tokens: {
      '--bg': '#9BD9F7',
      '--bg-2': '#FFC27A',
      '--surface': '#FFFBF1',
      '--surface-2': '#FFF3D6',
      '--surface-3': '#FFE6AE',
      '--ink': '#3A2014',
      '--ink-2': '#6E4A33',
      '--ink-3': '#9E7B60',
      '--line': '#4B2A1B',
      '--line-soft': '#E8CFA8',
      '--brand': '#F58A2E',
      '--brand-2': '#FFD23F',
      '--brand-ink': '#3A2014',
      '--btn-brand': '#F58A2E',
      '--btn-brand-2': '#FFD23F',
      '--accent': '#2EC4B6',
      '--accent-soft': '#D3F5F0',
      '--accent-ink': '#0B5A53',
      '--coral': '#FF6F59',
      '--go': '#7DCB2E',
      '--go-ink': '#22340A',
      '--warn': '#FFB400',
      '--danger': '#E04848',
      '--user-bubble': '#FFE59A',
      '--user-bubble-line': '#E3A21A',
      '--bot-bubble': '#FFF6E2',
      '--bot-bubble-line': '#E8CFA8',
      '--code-bg': '#F2EEE6',
      '--code-ink': '#3A2014',
      '--kw': '#7A3FC6',
      '--str': '#C23A2B',
      '--num': '#0E8A7E',
      '--fn': '#C25B00',
      '--radius': '16px',
      '--radius-sm': '10px',
      '--border-w': '2.5px',
      '--shadow': '3px 4px 0 #4B2A1B',
      '--glow': 'none',
      '--frame': 'linear-gradient(115deg,#7FD0F5 0%,#BFE3E9 38%,#FFD08A 70%,#FFA552 100%)',
      '--font-display': "'Fredoka', 'Nunito', system-ui, sans-serif",
      '--font-body': "'Nunito', system-ui, sans-serif",
      '--submit-shape': '999px',
      '--focus': '#7A3FC6',
      '--pill-ok-bg': '#DDF5B5',
      '--pill-ok-ink': '#3B6410',
      '--pill-warn-bg': '#FFE8A3',
      '--pill-warn-ink': '#6E4A00',
      '--pill-bad-bg': '#FFD3CB',
      '--pill-bad-ink': '#9C2414',
      '--color-scheme': 'light',
    },
    monaco: {
      base: 'vs',
      colors: {
        'editor.background': '#FFFBF1',
        'editorLineNumber.foreground': '#C7A887',
        'editor.lineHighlightBackground': '#FFF1CF',
      },
      rules: [
        { token: 'keyword', foreground: '7A3FC6', fontStyle: 'bold' },
        { token: 'string', foreground: 'C23A2B' },
        { token: 'number', foreground: '0E8A7E' },
        { token: 'comment', foreground: '9E7B60', fontStyle: 'italic' },
      ],
    },
    mermaid: {
      primaryColor: '#FFE6AE',
      primaryBorderColor: '#4B2A1B',
      primaryTextColor: '#3A2014',
      lineColor: '#4B2A1B',
      secondaryColor: '#D3F5F0',
      tertiaryColor: '#FFFBF1',
      attributeBackgroundColorOdd: '#FFFBF1',
      attributeBackgroundColorEven: '#FFF1CF',
      rowOdd: '#FFFBF1',
      rowEven: '#FFF1CF',
      fontFamily: 'Nunito, sans-serif',
    },
  },
  night: {
    name: 'night',
    brand: 'DataBark',
    mascot: 'Barky',
    label: 'Night',
    greeting: [
      "Working late? I'm right here with you. Let's get this schema sorted so you can get some rest.",
      "Good evening. The database is quiet tonight—perfect time to run some heavy queries. What's on your mind?",
      "Hey! Burning the midnight oil? I'm ready when you are.",
    ],
    tokens: {
      '--bg': '#16142F',
      '--bg-2': '#1C1A3A',
      '--surface': '#24214A',
      '--surface-2': '#2C2958',
      '--surface-3': '#353165',
      '--ink': '#F3F0FF',
      '--ink-2': '#BDB7E3',
      '--ink-3': '#8C86B8',
      '--line': '#4B4690',
      '--line-soft': '#37336A',
      '--brand': '#FF9F43',
      '--brand-2': '#FF6B6B',
      '--brand-ink': '#1C1A3A',
      '--btn-brand': '#FF9F43',
      '--btn-brand-2': '#FF6B6B',
      '--accent': '#5AA9FF',
      '--accent-soft': '#26345F',
      '--accent-ink': '#BFE0FF',
      '--coral': '#FF6B6B',
      '--go': '#3EF08F',
      '--go-ink': '#0E2A1B',
      '--warn': '#FFB347',
      '--danger': '#FF5C7A',
      '--user-bubble': '#2F3A75',
      '--user-bubble-line': '#5AA9FF',
      '--bot-bubble': '#312D60',
      '--bot-bubble-line': '#4B4690',
      '--code-bg': '#15132B',
      '--code-ink': '#E6E3FF',
      '--kw': '#6FB8FF',
      '--str': '#FF8A7A',
      '--num': '#FF8A7A',
      '--fn': '#C59BFF',
      '--radius': '18px',
      '--radius-sm': '12px',
      '--border-w': '1.5px',
      '--shadow': '0 0 0 1px rgba(90,169,255,.08), 0 18px 40px -20px rgba(0,0,0,.7)',
      '--glow': '0 0 18px rgba(90,169,255,.35)',
      '--frame': 'radial-gradient(1200px 600px at 20% -10%, #2B2763 0%, #16142F 60%)',
      '--font-display': "'Fredoka', 'Nunito', system-ui, sans-serif",
      '--font-body': "'Nunito', system-ui, sans-serif",
      '--submit-shape': '14px',
      '--focus': '#5AA9FF',
      '--pill-ok-bg': '#1F5C42',
      '--pill-ok-ink': '#8DF7BF',
      '--pill-warn-bg': '#6A4517',
      '--pill-warn-ink': '#FFD08A',
      '--pill-bad-bg': '#5E2335',
      '--pill-bad-ink': '#FFB3C1',
      '--color-scheme': 'dark',
    },
    monaco: {
      base: 'vs-dark',
      colors: {
        'editor.background': '#15132B',
        'editorLineNumber.foreground': '#5B5590',
        'editor.lineHighlightBackground': '#1F1C3F',
      },
      rules: [
        { token: 'keyword', foreground: '6FB8FF', fontStyle: 'bold' },
        { token: 'string', foreground: 'FF8A7A' },
        { token: 'number', foreground: 'FF8A7A' },
        { token: 'comment', foreground: '8C86B8', fontStyle: 'italic' },
      ],
    },
    mermaid: {
      primaryColor: '#2C2958',
      primaryBorderColor: '#5AA9FF',
      primaryTextColor: '#F3F0FF',
      lineColor: '#FF9F43',
      secondaryColor: '#353165',
      tertiaryColor: '#24214A',
      attributeBackgroundColorOdd: '#24214A',
      attributeBackgroundColorEven: '#2C2958',
      rowOdd: '#24214A',
      rowEven: '#2C2958',
      fontFamily: 'Nunito, sans-serif',
      background: '#24214A',
    },
  },
};

export const DEFAULT_WINDOWS = { morning: [5, 12], afternoon: [12, 18], night: [18, 5] } as Record<
  ThemeName,
  [number, number]
>;

export function themeForHour(hour: number, windows = DEFAULT_WINDOWS): ThemeName {
  for (const name of ['morning', 'afternoon', 'night'] as ThemeName[]) {
    const [s, e] = windows[name];
    if (s < e ? hour >= s && hour < e : hour >= s || hour < e) return name;
  }
  return 'night';
}

export function applyTheme(name: ThemeName) {
  const t = THEMES[name];
  const root = document.documentElement;
  root.dataset.theme = name;
  for (const [k, v] of Object.entries(t.tokens)) root.style.setProperty(k, v);
  root.style.colorScheme = t.tokens['--color-scheme'];
  document.title = `${t.brand} SQL Assistant`;
}

/** Chat greetings follow the user's actual local time of day, not the (possibly pinned) theme. */
export const TIME_GREETINGS: Record<'morning' | 'afternoon' | 'evening' | 'lateNight', string[]> = {
  morning: THEMES.morning.greeting,
  afternoon: THEMES.afternoon.greeting,
  evening: [
    "Good evening. The database is quiet tonight—perfect time to run some heavy queries. What's on your mind?",
    "Evening! Let's wrap up today's questions. What do you need?",
    "Good evening! I'm still here and happy to dig. What are we looking at?",
  ],
  lateNight: [
    "Working late? I'm right here with you. Let's get this schema sorted so you can get some rest.",
    "Hey! Burning the midnight oil? I'm ready when you are.",
    "It's late—let's make this quick and accurate. What do you need?",
  ],
};

export function timeOfDay(hour: number): keyof typeof TIME_GREETINGS {
  if (hour >= 5 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 18) return 'afternoon';
  if (hour >= 18 && hour < 22) return 'evening';
  return 'lateNight';
}

export function pickGreeting(now: Date = new Date()): string {
  const g = TIME_GREETINGS[timeOfDay(now.getHours())];
  return g[Math.floor(Math.random() * g.length)];
}
