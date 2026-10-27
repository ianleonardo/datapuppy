import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { get } from '../../api/client';
import { useApp } from '../../app/store';
import { copyText } from '../../lib/toast';
import { Mascot } from '../../mascot/Mascot';
import { THEMES } from '../../theme/themes';

let mermaidMod: typeof import('mermaid').default | null = null;
let seq = 0;

async function renderMermaid(source: string, theme: keyof typeof THEMES): Promise<string> {
  if (!mermaidMod) mermaidMod = (await import('mermaid')).default;
  mermaidMod.initialize({
    startOnLoad: false,
    securityLevel: 'strict', // TR-UI-03
    theme: 'base',
    themeVariables: THEMES[theme].mermaid,
    er: { useMaxWidth: false },
    fontFamily: 'Nunito, sans-serif',
  });
  const clean = source.replace(/<\/?script[^>]*>/gi, '').replace(/click\s+\S+/gi, '');
  await mermaidMod.parse(clean);
  const { svg } = await mermaidMod.render(`erd-${++seq}`, clean);
  return svg;
}

export function ErdPanel() {
  const me = useApp((s) => s.me)!;
  const erd = useApp((s) => s.erd);
  const setErd = useApp((s) => s.setErd);
  const connectionId = useApp((s) => s.connectionId);
  const [svg, setSvg] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // initial ERD for the selected connection
  useEffect(() => {
    if (!connectionId) return;
    get(`/schema/${connectionId}/erd`)
      .then((r) => setErd({ source: r.mermaid, title: 'Entity relationships' }, { focus: false }))
      .catch(() => undefined);
  }, [connectionId, setErd]);

  useEffect(() => {
    if (!erd) return;
    setErr(null);
    renderMermaid(erd.source, me.theme)
      .then((s) => setSvg(s))
      .catch((e) => setErr(String(e?.message || e).slice(0, 200)));
  }, [erd, me.theme]);

  useLayoutEffect(() => {
    if (svg) fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svg]);

  function fit() {
    const box = boxRef.current;
    const el = box?.querySelector('svg');
    if (!box || !el) return setView({ x: 0, y: 0, k: 1 });
    const vb = el.viewBox?.baseVal;
    const w = vb?.width || el.getBoundingClientRect().width;
    const h = vb?.height || el.getBoundingClientRect().height;
    el.setAttribute('width', String(w)); // mermaid emits width="100%"; pin intrinsic size so scale math holds
    el.setAttribute('height', String(h));
    el.style.maxWidth = 'none';
    const k = Math.max(
      0.55,
      Math.min(1.2, (box.clientWidth - 32) / w, (box.clientHeight - 32) / h),
    ); // stay legible; pan for the rest
    setView({
      x: Math.max(0, (box.clientWidth - 32 - w * k) / 2),
      y: Math.max(0, (box.clientHeight - 32 - h * k) / 2),
      k,
    });
  }

  if (!erd)
    return (
      <div className="grid-empty">
        <Mascot theme={me.theme} mood="sniffing" size={110} />
        <p>The ER diagram for your database shows up here once its schema is approved.</p>
      </div>
    );

  return (
    <div className="erd">
      <div className="erd-tools">
        <span className="erd-title">{erd.title || 'Diagram'}</span>
        <span className="grow" />
        <button
          className="icon-btn"
          onClick={() => setView((v) => ({ ...v, k: Math.min(3, v.k * 1.2) }))}
          aria-label="Zoom in"
        >
          ＋
        </button>
        <button
          className="icon-btn"
          onClick={() => setView((v) => ({ ...v, k: Math.max(0.12, v.k / 1.2) }))}
          aria-label="Zoom out"
        >
          －
        </button>
        <button className="icon-btn" onClick={fit} aria-label="Fit to panel">
          ⤢
        </button>
        <button
          className="btn btn-sm btn-ghost"
          onClick={() => copyText(erd.source, 'Mermaid source copied')}
        >
          Copy source
        </button>
        <button
          className="btn btn-sm btn-ghost"
          onClick={() => boxRef.current?.requestFullscreen?.()}
        >
          Full screen
        </button>
      </div>
      {err ? (
        <div className="erd-error">
          <p>I couldn't draw this diagram: {err}</p>
          <pre className="sqlcode">{erd.source}</pre>
        </div>
      ) : (
        <div
          className="erd-canvas"
          ref={boxRef}
          onWheel={(e) => {
            const k = Math.max(0.12, Math.min(3, view.k * (e.deltaY < 0 ? 1.1 : 0.9)));
            setView((v) => ({ ...v, k }));
          }}
          onPointerDown={(e) => {
            drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
            (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (drag.current)
              setView((v) => ({
                ...v,
                x: drag.current!.vx + e.clientX - drag.current!.x,
                y: drag.current!.vy + e.clientY - drag.current!.y,
              }));
          }}
          onPointerUp={() => (drag.current = null)}
        >
          <div
            className="erd-svg"
            style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      )}
    </div>
  );
}
