import { create } from 'zustand';

/**
 * Read chat replies aloud with the browser's built-in speech synthesis (no server call, works offline).
 * Long text is spoken in sentence-sized pieces: some browsers stop a single long utterance after ~15 s.
 */
export const speechSupported =
  typeof window !== 'undefined' &&
  'speechSynthesis' in window &&
  typeof SpeechSynthesisUtterance !== 'undefined';

interface SpeechState {
  speakingId: string | null;
  speak: (id: string, text: string) => void;
  stop: () => void;
}

let runToken = 0;

function pickVoice(): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  const lang = (navigator.language || 'en').toLowerCase();
  return (
    voices.find((v) => v.lang.toLowerCase() === lang && v.localService) ||
    voices.find((v) => v.lang.toLowerCase().startsWith(lang.slice(0, 2)) && v.default) ||
    voices.find((v) => v.lang.toLowerCase().startsWith('en') && v.localService) ||
    voices.find((v) => v.lang.toLowerCase().startsWith('en'))
  );
}

function chunks(text: string, max = 220): string[] {
  const out: string[] = [];
  for (const sentence of text.match(/[^.!?\n]+[.!?]*\s*|\n+/g) || [text]) {
    const s = sentence.trim();
    if (!s) continue;
    if (out.length && (out[out.length - 1] + ' ' + s).length <= max) out[out.length - 1] += ' ' + s;
    else if (s.length <= max) out.push(s);
    else for (let i = 0; i < s.length; i += max) out.push(s.slice(i, i + max));
  }
  return out;
}

export const useSpeech = create<SpeechState>((set) => ({
  speakingId: null,
  speak: (id, text) => {
    if (!speechSupported) return;
    const synth = window.speechSynthesis;
    synth.cancel();
    const token = ++runToken;
    const parts = chunks(text);
    if (!parts.length) return;
    const voice = pickVoice();
    set({ speakingId: id });
    parts.forEach((part, i) => {
      const u = new SpeechSynthesisUtterance(part);
      if (voice) {
        u.voice = voice;
        u.lang = voice.lang;
      }
      u.rate = 1;
      if (i === parts.length - 1)
        u.onend = () => {
          if (token === runToken) set({ speakingId: null });
        };
      u.onerror = () => {
        if (token === runToken) set({ speakingId: null });
      };
      synth.speak(u);
    });
  },
  stop: () => {
    runToken++;
    if (speechSupported) window.speechSynthesis.cancel();
    set({ speakingId: null });
  },
}));

/** Markdown → plain spoken text: drop code, keep link text, strip formatting characters. */
export function markdownToSpeech(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, ' (code shown on screen) ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\|?\s*:?-{2,}.*$/gm, '')
    .replace(/\|/g, ', ')
    .replace(/(\*\*|__|\*|_|~~)(\S[\s\S]*?\S|\S)\1/g, '$2')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim();
}
