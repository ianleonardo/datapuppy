import { memo } from 'react';
import type { ThemeName } from '../theme/themes';
import './mascot.css';

/**
 * Original puppy mascot (TS §16.1 / §17.1). Pure SVG + CSS so it stays tiny, bundled and offline.
 * Variants: morning = QueryPup (golden pup), afternoon = Tiangou (mythic heavenly dog), night = DataBark (beagle).
 * Moods: idle · thinking (digging) · happy · confused · protective · sniffing · sleepy · waking · excited.
 */
export type Mood =
  | 'idle'
  | 'thinking'
  | 'happy'
  | 'confused'
  | 'protective'
  | 'sniffing'
  | 'sleepy'
  | 'waking'
  | 'excited';

const PALETTE: Record<ThemeName, Record<string, string>> = {
  morning: {
    fur: '#F2B866',
    furDark: '#DB9440',
    ear: '#D3873A',
    cream: '#FFF3E0',
    line: '#7A4A22',
    scarf: '#E5484D',
    scarfDark: '#B8333A',
    nose: '#3B2416',
  },
  afternoon: {
    fur: '#F7BE55',
    furDark: '#EC9A33',
    ear: '#F2A43C',
    cream: '#FFF5E2',
    line: '#4B2A1B',
    scarf: '#E2453C',
    scarfDark: '#A92F28',
    nose: '#3B2416',
  },
  night: {
    fur: '#DE9550',
    furDark: '#7E4A2A',
    ear: '#6A3B20',
    cream: '#FFF6EA',
    line: '#2B1A10',
    scarf: '#FF5C6C',
    scarfDark: '#C23A4C',
    nose: '#22140C',
  },
};
const RAINBOW = ['#FF5E5B', '#FF9F1C', '#FFD23F', '#7BD389', '#4CC9F0', '#9B5DE5'];

interface Props {
  theme: ThemeName;
  mood?: Mood;
  size?: number | string;
  headOnly?: boolean;
  coverEyes?: boolean;
  className?: string;
  title?: string;
}

function Swirl({
  cx,
  cy,
  r,
  color,
  line,
}: {
  cx: number;
  cy: number;
  r: number;
  color: string;
  line: string;
}) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill={color} stroke={line} strokeWidth={2.5} />
      <path
        d={`M${cx - r * 0.45} ${cy} a${r * 0.45} ${r * 0.45} 0 1 1 ${r * 0.45} ${r * 0.45} a${r * 0.22} ${r * 0.22} 0 1 1 ${-r * 0.22} ${-r * 0.22}`}
        fill="none"
        stroke={line}
        strokeWidth={2}
        strokeLinecap="round"
        opacity={0.75}
      />
    </g>
  );
}

function MascotImpl({
  theme,
  mood = 'idle',
  size = 160,
  headOnly,
  coverEyes,
  className,
  title,
}: Props) {
  const p = PALETTE[theme];
  const sw = theme === 'afternoon' ? 3.2 : 2.6;
  const tiangou = theme === 'afternoon';
  const beagle = theme === 'night';
  const vb = headOnly ? '44 18 132 124' : '0 0 220 220';
  const label =
    title ??
    `${theme === 'morning' ? 'QueryPup' : theme === 'afternoon' ? 'Tiangou' : 'Barky'} is ${mood}`;
  return (
    <svg
      className={`pup ${className ?? ''}`}
      data-mood={mood}
      data-theme={theme}
      data-cover={coverEyes ? '1' : '0'}
      viewBox={vb}
      width={size}
      height={size}
      role="img"
      aria-label={label}
    >
      <g className="pup-all">
        {!headOnly && (
          <ellipse
            className="pup-shadow"
            cx="110"
            cy="206"
            rx="54"
            ry="7"
            fill="rgba(40,20,0,.16)"
          />
        )}
        <g className="pup-jump">
          {/* tail */}
          {!headOnly && (
            <g className="pup-tail">
              <path
                d="M146 168 C 176 166, 190 138, 176 116 C 170 132, 160 150, 142 154 Z"
                fill={tiangou ? '#FF9F1C' : p.furDark}
                stroke={p.line}
                strokeWidth={sw}
                strokeLinejoin="round"
              />
              {tiangou && (
                <>
                  <path
                    d="M176 118 C 186 128, 184 146, 170 156"
                    fill="none"
                    stroke="#FF5E5B"
                    strokeWidth="4"
                    strokeLinecap="round"
                  />
                  <path
                    d="M170 124 C 176 134, 172 148, 162 154"
                    fill="none"
                    stroke="#FFD23F"
                    strokeWidth="4"
                    strokeLinecap="round"
                  />
                  <circle
                    cx="178"
                    cy="116"
                    r="6"
                    fill="#9B5DE5"
                    stroke={p.line}
                    strokeWidth="2.5"
                  />
                </>
              )}
            </g>
          )}
          {/* body */}
          {!headOnly && (
            <g className="pup-body">
              <ellipse
                cx="110"
                cy="164"
                rx="45"
                ry="38"
                fill={p.fur}
                stroke={p.line}
                strokeWidth={sw}
              />
              {beagle && (
                <path
                  d="M78 150 Q110 128 142 150 Q140 170 110 170 Q80 170 78 150 Z"
                  fill={p.furDark}
                  opacity=".9"
                />
              )}
              <ellipse cx="110" cy="174" rx="25" ry="24" fill={p.cream} />
              <ellipse
                cx="80"
                cy="197"
                rx="17"
                ry="9.5"
                fill={p.fur}
                stroke={p.line}
                strokeWidth={sw}
              />
              <ellipse
                cx="140"
                cy="197"
                rx="17"
                ry="9.5"
                fill={p.fur}
                stroke={p.line}
                strokeWidth={sw}
              />
              <g className="pup-paw-l">
                <ellipse
                  cx="97"
                  cy="199"
                  rx="12"
                  ry="8.5"
                  fill={p.cream}
                  stroke={p.line}
                  strokeWidth={sw}
                />
                <path
                  d="M93 196 v5 M99 196 v5"
                  stroke={p.line}
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
              </g>
              <g className="pup-paw-r">
                <ellipse
                  cx="123"
                  cy="199"
                  rx="12"
                  ry="8.5"
                  fill={p.cream}
                  stroke={p.line}
                  strokeWidth={sw}
                />
                <path
                  d="M121 196 v5 M127 196 v5"
                  stroke={p.line}
                  strokeWidth="1.6"
                  strokeLinecap="round"
                />
              </g>
              {/* raised "stop" paw — protective / guardian mode */}
              <g className="pup-paw-raise">
                <path
                  d="M126 160 Q140 140 150 120"
                  stroke={p.line}
                  strokeWidth={sw + 15}
                  strokeLinecap="round"
                  fill="none"
                />
                <path
                  d="M126 160 Q140 140 150 120"
                  stroke={p.fur}
                  strokeWidth={15}
                  strokeLinecap="round"
                  fill="none"
                />
                <circle cx="152" cy="114" r="14" fill={p.cream} stroke={p.line} strokeWidth={sw} />
                <ellipse cx="152" cy="118" rx="5.5" ry="4.5" fill="#FF8FA3" />
                <circle cx="145" cy="109" r="2.6" fill="#FF8FA3" />
                <circle cx="152" cy="106" r="2.6" fill="#FF8FA3" />
                <circle cx="159" cy="109" r="2.6" fill="#FF8FA3" />
              </g>
            </g>
          )}
          {/* neckwear */}
          {tiangou ? (
            <g className="pup-collar">
              <path
                d="M74 128 Q110 146 146 128"
                stroke={p.line}
                strokeWidth={sw + 9}
                fill="none"
                strokeLinecap="round"
              />
              <path
                d="M74 128 Q110 146 146 128"
                stroke={p.scarf}
                strokeWidth={9}
                fill="none"
                strokeLinecap="round"
              />
              <g className="pup-bell">
                <circle cx="110" cy="147" r="10" fill="#FFC93C" stroke={p.line} strokeWidth={sw} />
                <path d="M102 146 h16" stroke={p.line} strokeWidth="2" />
                <circle cx="110" cy="151" r="2" fill={p.line} />
              </g>
            </g>
          ) : (
            <g className="pup-scarf">
              <path
                d="M74 126 Q110 142 146 126 L138 136 Q118 160 110 166 Q102 160 82 136 Z"
                fill={p.scarf}
                stroke={p.line}
                strokeWidth={sw}
                strokeLinejoin="round"
              />
              <circle cx="98" cy="140" r="2" fill="#fff" opacity=".8" />
              <circle cx="118" cy="146" r="2" fill="#fff" opacity=".8" />
              <circle cx="110" cy="134" r="2" fill="#fff" opacity=".8" />
            </g>
          )}
          {/* head */}
          <g className="pup-head">
            {tiangou && (
              <g className="pup-mane">
                {[
                  [62, 74, 13],
                  [58, 102, 12],
                  [70, 124, 11],
                  [158, 74, 13],
                  [162, 102, 12],
                  [150, 124, 11],
                  [80, 50, 12],
                  [140, 50, 12],
                  [110, 42, 11],
                ].map(([x, y, r], i) => (
                  <Swirl
                    key={i}
                    cx={x}
                    cy={y}
                    r={r}
                    color={RAINBOW[i % RAINBOW.length]}
                    line={p.line}
                  />
                ))}
                <g className="pup-horns">
                  <path
                    d="M90 50 Q80 30 90 18 Q94 30 100 34 Q104 26 104 22 Q110 34 100 46 Z"
                    fill="#9AD06A"
                    stroke={p.line}
                    strokeWidth="2.6"
                    strokeLinejoin="round"
                  />
                  <path
                    d="M130 50 Q140 30 130 18 Q126 30 120 34 Q116 26 116 22 Q110 34 120 46 Z"
                    fill="#9AD06A"
                    stroke={p.line}
                    strokeWidth="2.6"
                    strokeLinejoin="round"
                  />
                </g>
              </g>
            )}
            <g className="pup-ear-l">
              <path
                d="M72 60 C 48 54, 34 90, 44 118 C 54 124, 66 110, 74 92 Z"
                fill={p.ear}
                stroke={p.line}
                strokeWidth={sw}
                strokeLinejoin="round"
              />
            </g>
            <g className="pup-ear-r">
              <path
                d="M148 60 C 172 54, 186 90, 176 118 C 166 124, 154 110, 146 92 Z"
                fill={p.ear}
                stroke={p.line}
                strokeWidth={sw}
                strokeLinejoin="round"
              />
            </g>
            <ellipse
              cx="110"
              cy="90"
              rx="48"
              ry="42"
              fill={p.fur}
              stroke={p.line}
              strokeWidth={sw}
            />
            {beagle && <path d="M103 50 Q110 46 117 50 L121 92 Q110 98 99 92 Z" fill={p.cream} />}
            {tiangou && (
              <>
                <path
                  d="M84 72 q6 -6 12 0"
                  stroke="#FF5E5B"
                  strokeWidth="3"
                  fill="none"
                  strokeLinecap="round"
                />
                <path
                  d="M124 72 q6 -6 12 0"
                  stroke="#FF5E5B"
                  strokeWidth="3"
                  fill="none"
                  strokeLinecap="round"
                />
              </>
            )}
            <ellipse cx="110" cy="109" rx="25" ry="17" fill={p.cream} />
            <circle className="pup-cheek" cx="78" cy="106" r="6.5" fill="#FF8FA3" opacity=".45" />
            <circle className="pup-cheek" cx="142" cy="106" r="6.5" fill="#FF8FA3" opacity=".45" />
            <g className="pup-brows">
              <path d="M80 76 L98 82" stroke={p.line} strokeWidth="3.4" strokeLinecap="round" />
              <path d="M140 76 L122 82" stroke={p.line} strokeWidth="3.4" strokeLinecap="round" />
            </g>
            <g className="pup-brows-worry">
              <path d="M80 80 L97 76" stroke={p.line} strokeWidth="3" strokeLinecap="round" />
              <path d="M140 74 L123 79" stroke={p.line} strokeWidth="3" strokeLinecap="round" />
            </g>
            <g className="pup-eyes">
              <ellipse cx="90" cy="90" rx="7.5" ry="8" fill={p.nose} />
              <ellipse cx="130" cy="90" rx="7.5" ry="8" fill={p.nose} />
              <circle cx="92.6" cy="87" r="2.8" fill="#fff" />
              <circle cx="132.6" cy="87" r="2.8" fill="#fff" />
              <circle cx="88" cy="93" r="1.2" fill="#fff" opacity=".8" />
              <circle cx="128" cy="93" r="1.2" fill="#fff" opacity=".8" />
            </g>
            <g className="pup-eyes-closed">
              <path
                d="M82 91 Q90 97 98 91"
                stroke={p.line}
                strokeWidth="3"
                fill="none"
                strokeLinecap="round"
              />
              <path
                d="M122 91 Q130 97 138 91"
                stroke={p.line}
                strokeWidth="3"
                fill="none"
                strokeLinecap="round"
              />
            </g>
            <g className="pup-eyes-happy">
              <path
                d="M82 93 Q90 84 98 93"
                stroke={p.line}
                strokeWidth="3.2"
                fill="none"
                strokeLinecap="round"
              />
              <path
                d="M122 93 Q130 84 138 93"
                stroke={p.line}
                strokeWidth="3.2"
                fill="none"
                strokeLinecap="round"
              />
            </g>
            <g className="pup-nose">
              <ellipse cx="110" cy="101" rx="7.5" ry="5.2" fill={p.nose} />
              <ellipse cx="108" cy="99.4" rx="2.4" ry="1.4" fill="#fff" opacity=".7" />
            </g>
            <path
              className="pup-mouth"
              d="M101 109 Q105.5 114 110 109 Q114.5 114 119 109"
              stroke={p.line}
              strokeWidth="2.6"
              fill="none"
              strokeLinecap="round"
            />
            <path
              className="pup-mouth-flat"
              d="M103 112 Q110 109 117 112"
              stroke={p.line}
              strokeWidth="2.6"
              fill="none"
              strokeLinecap="round"
            />
            <path
              className="pup-tongue"
              d="M105 111 Q110 126 115 111 Z"
              fill="#FF7B8A"
              stroke={p.line}
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <ellipse
              className="pup-yawn"
              cx="110"
              cy="115"
              rx="6.5"
              ry="8"
              fill="#5A2A2A"
              stroke={p.line}
              strokeWidth="2"
            />
            {/* paws over eyes (login password focus) */}
            <g className="pup-cover">
              <ellipse
                cx="89"
                cy="92"
                rx="16"
                ry="12.5"
                fill={p.cream}
                stroke={p.line}
                strokeWidth={sw}
              />
              <ellipse
                cx="131"
                cy="92"
                rx="16"
                ry="12.5"
                fill={p.cream}
                stroke={p.line}
                strokeWidth={sw}
              />
              <path
                d="M84 88 v7 M91 88 v7 M126 88 v7 M133 88 v7"
                stroke={p.line}
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </g>
          </g>
        </g>
        {/* mood overlays */}
        {!headOnly && (
          <g className="pup-overlays">
            <g className="ov ov-think">
              <circle
                cx="158"
                cy="52"
                r="3.5"
                fill="var(--surface, #fff)"
                stroke={p.line}
                strokeWidth="2"
              />
              <circle
                cx="168"
                cy="40"
                r="5.5"
                fill="var(--surface, #fff)"
                stroke={p.line}
                strokeWidth="2"
              />
              <ellipse
                cx="192"
                cy="20"
                rx="25"
                ry="16"
                fill="var(--surface, #fff)"
                stroke={p.line}
                strokeWidth="2.2"
              />
              <circle className="dot d1" cx="182" cy="20" r="3" fill="var(--brand, #F28C28)" />
              <circle className="dot d2" cx="192" cy="20" r="3" fill="var(--brand, #F28C28)" />
              <circle className="dot d3" cx="202" cy="20" r="3" fill="var(--brand, #F28C28)" />
            </g>
            <g className="ov ov-dig">
              <circle cx="70" cy="200" r="3" fill="#B98552" />
              <circle cx="60" cy="194" r="2.4" fill="#B98552" />
              <circle cx="150" cy="200" r="3" fill="#B98552" />
              <circle cx="162" cy="193" r="2.2" fill="#B98552" />
            </g>
            <text
              className="ov ov-question"
              x="166"
              y="48"
              fontFamily="Fredoka, sans-serif"
              fontWeight="700"
              fontSize="40"
              fill="var(--brand, #F28C28)"
              stroke={p.line}
              strokeWidth="1.5"
            >
              ?
            </text>
            <g className="ov ov-woof">
              <path
                d="M150 10 h56 a10 10 0 0 1 10 10 v14 a10 10 0 0 1 -10 10 h-36 l-10 10 l0 -10 h-10 a10 10 0 0 1 -10 -10 v-14 a10 10 0 0 1 10 -10 Z"
                fill="var(--surface, #fff)"
                stroke={p.line}
                strokeWidth="2.2"
              />
              <text
                x="178"
                y="33"
                textAnchor="middle"
                fontFamily="Fredoka, sans-serif"
                fontWeight="700"
                fontSize="15"
                fill="var(--brand, #F28C28)"
              >
                Woof!
              </text>
            </g>
            <g className="ov ov-alert">
              <circle
                cx="186"
                cy="30"
                r="17"
                fill="var(--warn, #F2A93B)"
                stroke={p.line}
                strokeWidth="2.4"
              />
              <text
                x="186"
                y="39"
                textAnchor="middle"
                fontFamily="Fredoka, sans-serif"
                fontWeight="700"
                fontSize="24"
                fill={p.line}
              >
                !
              </text>
            </g>
            <g
              className="ov ov-sniff"
              stroke={p.line}
              strokeWidth="2.2"
              fill="none"
              strokeLinecap="round"
            >
              <path className="s1" d="M74 96 q-6 4 0 8 q6 4 0 8" />
              <path className="s2" d="M62 92 q-6 4 0 8 q6 4 0 8" />
              <path className="s3" d="M50 88 q-6 4 0 8 q6 4 0 8" />
            </g>
            <g
              className="ov ov-zzz"
              fontFamily="Fredoka, sans-serif"
              fontWeight="700"
              fill="var(--accent, #8ECDF7)"
              stroke={p.line}
              strokeWidth="1"
            >
              <text className="z1" x="150" y="56" fontSize="16">
                z
              </text>
              <text className="z2" x="166" y="40" fontSize="22">
                z
              </text>
              <text className="z3" x="184" y="22" fontSize="28">
                Z
              </text>
            </g>
            <g
              className="ov ov-sparkle"
              fill="var(--brand-2, #FFD23F)"
              stroke={p.line}
              strokeWidth="1.6"
            >
              <path className="sp1" d="M30 60 l4 10 l10 4 l-10 4 l-4 10 l-4 -10 l-10 -4 l10 -4 Z" />
              <path className="sp2" d="M186 70 l3 8 l8 3 l-8 3 l-3 8 l-3 -8 l-8 -3 l8 -3 Z" />
              <path className="sp3" d="M172 18 l3 7 l7 3 l-7 3 l-3 7 l-3 -7 l-7 -3 l7 -3 Z" />
              <path className="sp4" d="M40 140 l3 7 l7 3 l-7 3 l-3 7 l-3 -7 l-7 -3 l7 -3 Z" />
            </g>
          </g>
        )}
      </g>
    </svg>
  );
}

export const Mascot = memo(MascotImpl);
