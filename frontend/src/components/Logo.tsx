"use client";

/** PricePulse brand mark — pulse line on a rounded indigo tile.
 *  Inline SVG so it stays crisp + animates on hover/load. */

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-label="PricePulse"
      className="logo-mark"
    >
      <defs>
        <linearGradient id="pp-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0b0d12" />
          <stop offset="1" stopColor="#1f1f3a" />
        </linearGradient>
        <linearGradient id="pp-pulse" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0"   stopColor="#a5b4fc" />
          <stop offset="0.5" stopColor="#ffffff" />
          <stop offset="1"   stopColor="#f0abfc" />
        </linearGradient>
      </defs>

      <rect width="32" height="32" rx="9" fill="url(#pp-grad)" />

      {/* ECG / pulse line */}
      <path
        d="M4 16 H10 L12 11 L15 21 L18 13 L20.5 18 H28"
        fill="none"
        stroke="url(#pp-pulse)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="logo-stroke"
      />

      {/* end-dot */}
      <circle cx="28" cy="18" r="2" fill="#a5b4fc" className="logo-dot" />

      <style>{`
        .logo-mark .logo-stroke {
          stroke-dasharray: 60;
          stroke-dashoffset: 60;
          animation: pp-draw 1.1s cubic-bezier(.16,1,.3,1) .15s forwards;
        }
        .logo-mark .logo-dot {
          transform-origin: 28px 18px;
          animation: pp-pulse 1.6s ease-in-out 1.2s infinite;
          opacity: 0;
        }
        @keyframes pp-draw  { to { stroke-dashoffset: 0; } }
        @keyframes pp-pulse {
          0%   { opacity: .35; transform: scale(.8); }
          50%  { opacity:   1; transform: scale(1.3); }
          100% { opacity: .35; transform: scale(.8); }
        }
        .logo-mark:hover .logo-stroke { animation: pp-redraw 1.0s cubic-bezier(.16,1,.3,1); }
        @keyframes pp-redraw { from { stroke-dashoffset: 60; } to { stroke-dashoffset: 0; } }
      `}</style>
    </svg>
  );
}
