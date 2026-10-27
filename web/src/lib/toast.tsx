import { create } from 'zustand';

interface Toast {
  id: number;
  text: string;
  err?: boolean;
}
interface ToastState {
  items: Toast[];
  push: (text: string, err?: boolean) => void;
}
let n = 0;
export const useToasts = create<ToastState>((set) => ({
  items: [],
  push: (text, err) => {
    const id = ++n;
    set((s) => ({ items: [...s.items, { id, text, err }] }));
    setTimeout(
      () => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
      err ? 6000 : 2600,
    );
  },
}));
export const toast = (text: string) => useToasts.getState().push(text);
export const toastError = (text: string) => useToasts.getState().push(text, true);

export function Toasts() {
  const items = useToasts((s) => s.items);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.err ? 'err' : ''}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

/** Copy with navigator.clipboard, falling back to a textarea for plain-HTTP intranets (TS §16.4). */
export async function copyText(text: string, what = 'Copied to clipboard') {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast(what);
  } catch {
    toastError("Couldn't copy — select the text and press Ctrl+C.");
  }
}
