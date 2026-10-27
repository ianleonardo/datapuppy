import { useMemo } from 'react';
import type { ThemeName } from '../../theme/themes';

/* ============================================================================================
 * Login backdrops — one distinct animation style per theme.
 *   morning   → "Sunrise fetch": sun rising over hills, drifting clouds, a trotting paw-print trail.
 *   afternoon → "Heavenly dog": auspicious xiangyun clouds, twinkling sparkles, the sun being nibbled
 *               (the Tiangou eclipse myth), floating paw confetti.
 *   night     → "Data constellation": star field, a neon constellation that draws a dog's head,
 *               packets of light travelling along its edges, shooting stars, drifting SQL keywords.
 * ========================================================================================== */

function Paw({
  x,
  y,
  r = 1,
  rot = 0,
  className,
  fill,
}: {
  x: number;
  y: number;
  r?: number;
  rot?: number;
  className?: string;
  fill: string;
}) {
  return (
    <g transform={`translate(${x} ${y}) rotate(${rot}) scale(${r})`} className={className}>
      <ellipse cx="0" cy="6" rx="9" ry="7.5" fill={fill} />
      <ellipse cx="-9" cy="-4" rx="3.6" ry="4.6" fill={fill} />
      <ellipse cx="-3" cy="-9" rx="3.6" ry="4.6" fill={fill} />
      <ellipse cx="4" cy="-9" rx="3.6" ry="4.6" fill={fill} />
      <ellipse cx="10" cy="-4" rx="3.6" ry="4.6" fill={fill} />
    </g>
  );
}

function Cloud({ className, scale = 1 }: { className?: string; scale?: number }) {
  return (
    <svg
      className={className}
      viewBox="0 0 220 90"
      width={220 * scale}
      height={90 * scale}
      aria-hidden="true"
    >
      <path
        d="M30 78 Q4 78 8 58 Q12 38 38 44 Q42 16 74 18 Q98 0 124 20 Q150 6 168 30 Q204 28 206 56 Q214 80 186 80 Z"
        fill="#FFFFFF"
        opacity=".95"
      />
    </svg>
  );
}

export function MorningScene() {
  const paws = useMemo(
    () => Array.from({ length: 9 }, (_, i) => ({ x: 60 + i * 160, y: i % 2 ? 618 : 640, rot: 80 })),
    [],
  );
  return (
    <div className="scene scene-morning" aria-hidden="true">
      <div className="m-sun">
        <svg viewBox="-160 -160 320 320" width="320" height="320">
          <g className="m-rays">
            {Array.from({ length: 16 }, (_, i) => (
              <rect
                key={i}
                x="-5"
                y="-150"
                width="10"
                height="44"
                rx="5"
                fill="#FFC86B"
                transform={`rotate(${i * 22.5})`}
                opacity=".7"
              />
            ))}
          </g>
          <circle r="92" fill="#FFB347" />
          <circle r="80" fill="#FFCB5C" />
          <circle cx="-26" cy="-26" r="22" fill="#FFE29A" opacity=".7" />
        </svg>
      </div>
      <Cloud className="m-cloud c1" scale={1.1} />
      <Cloud className="m-cloud c2" scale={0.7} />
      <Cloud className="m-cloud c3" scale={0.9} />
      <svg className="m-hills" viewBox="0 0 1440 320" preserveAspectRatio="none" aria-hidden="true">
        <path d="M0 140 Q240 60 520 120 T1040 110 T1440 90 V320 H0 Z" fill="#FFD497" />
        <path d="M0 200 Q300 130 640 190 T1440 170 V320 H0 Z" fill="#F9BE73" />
        <path d="M0 260 Q420 210 860 250 T1440 240 V320 H0 Z" fill="#F4A95A" />
      </svg>
      <svg className="m-trail" viewBox="0 0 1440 760" preserveAspectRatio="xMidYMax slice">
        {paws.map((p, i) => (
          <Paw
            key={i}
            x={p.x}
            y={p.y}
            rot={p.rot}
            r={1.1}
            fill="#C9782F"
            className={`m-paw p${i}`}
          />
        ))}
      </svg>
    </div>
  );
}

function Xiangyun({ className, w = 260 }: { className?: string; w?: number }) {
  // Auspicious cloud: rounded body with curled spiral ends, inked like a sketch.
  return (
    <svg
      className={className}
      viewBox="0 0 260 110"
      width={w}
      height={(w * 110) / 260}
      aria-hidden="true"
    >
      <path
        d="M20 86 Q6 86 8 70 Q10 54 28 56 Q30 30 60 32 Q74 8 104 18 Q124 2 148 20 Q176 10 186 36 Q214 30 222 54 Q246 56 246 74 Q246 90 226 90 Z"
        fill="#FFF7E6"
        stroke="#4B2A1B"
        strokeWidth="3.5"
        strokeLinejoin="round"
      />
      <path
        d="M60 60 q-14 -2 -12 -14 q4 -10 14 -6 q6 4 2 10"
        fill="none"
        stroke="#4B2A1B"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M150 52 q16 -4 16 -16 q-2 -10 -12 -8 q-8 4 -4 10"
        fill="none"
        stroke="#4B2A1B"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M200 72 q10 0 12 -8"
        fill="none"
        stroke="#4B2A1B"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Sparkle({
  x,
  y,
  s,
  color,
  d,
}: {
  x: string;
  y: string;
  s: number;
  color: string;
  d: number;
}) {
  return (
    <svg
      className="a-sparkle"
      style={{ left: x, top: y, animationDelay: `${d}s` }}
      width={s}
      height={s}
      viewBox="-10 -10 20 20"
      aria-hidden="true"
    >
      <path
        d="M0 -10 Q1.6 -1.6 10 0 Q1.6 1.6 0 10 Q-1.6 1.6 -10 0 Q-1.6 -1.6 0 -10 Z"
        fill={color}
        stroke="#4B2A1B"
        strokeWidth="1.3"
      />
    </svg>
  );
}

export function AfternoonScene() {
  const sparkles = [
    { x: '6%', y: '18%', s: 34, c: '#FFD23F', d: 0 },
    { x: '14%', y: '72%', s: 26, c: '#9B5DE5', d: 0.6 },
    { x: '88%', y: '22%', s: 38, c: '#4CC9F0', d: 1.1 },
    { x: '93%', y: '58%', s: 28, c: '#FFD23F', d: 1.7 },
    { x: '48%', y: '8%', s: 22, c: '#9B5DE5', d: 2.2 },
    { x: '70%', y: '86%', s: 30, c: '#4CC9F0', d: 0.3 },
    { x: '30%', y: '90%', s: 20, c: '#FF5E5B', d: 1.4 },
    { x: '78%', y: '8%', s: 18, c: '#FF9F1C', d: 2.6 },
  ];
  const confetti = Array.from({ length: 14 }, (_, i) => ({
    x: `${(i * 37) % 100}%`,
    delay: (i * 0.9) % 9,
    dur: 10 + (i % 5) * 2,
    paw: i % 3 === 0,
    color: ['#FFFFFF', '#FFD23F', '#4B2A1B', '#FF9F1C'][i % 4],
  }));
  return (
    <div className="scene scene-afternoon" aria-hidden="true">
      <div className="a-sun">
        <svg viewBox="-120 -120 240 240" width="240" height="240">
          <defs>
            <mask id="eclipse">
              <rect x="-120" y="-120" width="240" height="240" fill="#fff" />
              <circle className="a-bite" cx="112" cy="-44" r="50" fill="#000" />
            </mask>
          </defs>
          <g mask="url(#eclipse)">
            <circle r="78" fill="#FFD23F" stroke="#4B2A1B" strokeWidth="4" />
            <circle r="60" fill="#FFE27A" />
          </g>
          {Array.from({ length: 12 }, (_, i) => (
            <rect
              key={i}
              x="-5"
              y="-112"
              width="10"
              height="20"
              rx="5"
              fill="#FFB400"
              stroke="#4B2A1B"
              strokeWidth="2.2"
              transform={`rotate(${i * 30})`}
              className="a-ray"
            />
          ))}
        </svg>
      </div>
      <Xiangyun className="a-cloud k1" w={300} />
      <Xiangyun className="a-cloud k2" w={220} />
      <Xiangyun className="a-cloud k3" w={260} />
      <Xiangyun className="a-cloud k4" w={180} />
      {sparkles.map((s, i) => (
        <Sparkle key={i} x={s.x} y={s.y} s={s.s} color={s.c} d={s.d} />
      ))}
      <div className="a-confetti">
        {confetti.map((c, i) => (
          <span
            key={i}
            style={{ left: c.x, animationDelay: `${c.delay}s`, animationDuration: `${c.dur}s` }}
          >
            {c.paw ? (
              <svg width="22" height="22" viewBox="-14 -16 28 28">
                <Paw x={0} y={0} fill={c.color === '#FFFFFF' ? '#4B2A1B' : c.color} r={0.8} />
              </svg>
            ) : (
              <i style={{ background: c.color }} />
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

// Dog-head constellation (node coordinates in a 400×300 box)
const N = {
  A: [118, 64],
  B: [84, 158],
  C: [146, 116],
  D: [200, 70],
  E: [254, 116],
  G: [282, 64],
  F: [316, 158],
  H: [158, 196],
  I: [242, 196],
  K: [200, 186],
  J: [200, 236],
  L: [176, 140],
  M: [224, 140],
} as const;
const EDGES: [keyof typeof N, keyof typeof N][] = [
  ['A', 'B'],
  ['A', 'C'],
  ['C', 'D'],
  ['D', 'E'],
  ['E', 'G'],
  ['G', 'F'],
  ['B', 'H'],
  ['F', 'I'],
  ['C', 'H'],
  ['E', 'I'],
  ['H', 'K'],
  ['I', 'K'],
  ['K', 'J'],
  ['H', 'J'],
  ['I', 'J'],
];

export function NightScene() {
  const stars = useMemo(() => {
    let seed = 7;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    return Array.from({ length: 110 }, () => ({
      x: rnd() * 100,
      y: rnd() * 100,
      r: rnd() * 1.6 + 0.4,
      d: rnd() * 5,
      t: 2 + rnd() * 4,
    }));
  }, []);
  const words = [
    'SELECT',
    'JOIN',
    'WHERE',
    'GROUP BY',
    'LAG()',
    'WITH',
    'ORDER BY',
    'COUNT(*)',
    'OVER',
    'FETCH FIRST',
  ];
  const route =
    'M84 158 L118 64 L146 116 L200 70 L254 116 L282 64 L316 158 L242 196 L200 236 L158 196 L84 158';
  return (
    <div className="scene scene-night" aria-hidden="true">
      <svg className="n-stars" width="100%" height="100%">
        {stars.map((s, i) => (
          <circle
            key={i}
            cx={`${s.x}%`}
            cy={`${s.y}%`}
            r={s.r}
            fill="#fff"
            style={{ animationDelay: `${s.d}s`, animationDuration: `${s.t}s` }}
          />
        ))}
      </svg>
      <div className="n-moon">
        <svg viewBox="0 0 120 120" width="120" height="120">
          <defs>
            <mask id="crescent">
              <rect width="120" height="120" fill="#fff" />
              <circle cx="78" cy="46" r="40" fill="#000" />
            </mask>
          </defs>
          <circle cx="60" cy="60" r="44" fill="#FFE7B0" mask="url(#crescent)" />
        </svg>
      </div>
      <span className="n-shoot s1" />
      <span className="n-shoot s2" />
      <span className="n-shoot s3" />
      <svg className="n-constellation" viewBox="0 0 400 300">
        <defs>
          <filter id="neon" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g filter="url(#neon)">
          {EDGES.map(([a, b], i) => (
            <line
              key={i}
              className="n-edge"
              x1={N[a][0]}
              y1={N[a][1]}
              x2={N[b][0]}
              y2={N[b][1]}
              style={{ animationDelay: `${0.25 + i * 0.12}s` }}
            />
          ))}
          {Object.entries(N).map(([k, [x, y]], i) => (
            <circle
              key={k}
              className={`n-node ${k === 'K' ? 'nose' : ''} ${k === 'L' || k === 'M' ? 'eye' : ''}`}
              cx={x}
              cy={y}
              r={k === 'K' ? 7 : k === 'L' || k === 'M' ? 5 : 4.5}
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
          {[0, 1.6, 3.2].map((d, i) => (
            <circle key={i} r="3.5" className="n-packet" fill={i === 1 ? '#3EF08F' : '#FF9F43'}>
              <animateMotion
                dur="4.8s"
                begin={`${2.4 + d}s`}
                repeatCount="indefinite"
                path={route}
              />
            </circle>
          ))}
        </g>
      </svg>
      <div className="n-words">
        {words.map((w, i) => (
          <span key={w} style={{ left: `${6 + ((i * 9.5) % 90)}%`, animationDelay: `${i * 1.7}s` }}>
            {w}
          </span>
        ))}
      </div>
      <div className="n-horizon" />
    </div>
  );
}

export function Scene({ theme }: { theme: ThemeName }) {
  if (theme === 'morning') return <MorningScene />;
  if (theme === 'afternoon') return <AfternoonScene />;
  return <NightScene />;
}
