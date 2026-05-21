"use client";

/** Custom chat icon — a speech bubble that wraps a pulsing dot trio.
 *  Replaces lucide's generic <Bot/>. Inherits `currentColor`. */

export function ChatBubbleIcon({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 12c0 4.418-4.03 8-9 8-1.06 0-2.08-.16-3.02-.46L4 21l1.3-3.5C4.48 16.18 4 14.64 4 13c0-4.42 4.03-8 9-8 4.97 0 8 3.58 8 7z" />
      <g className="chat-dots">
        <circle cx="9"  cy="12" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none" />
        <circle cx="15" cy="12" r="1.1" fill="currentColor" stroke="none" />
      </g>
      <style>{`
        .chat-dots circle { opacity: .55; animation: chat-blink 1.4s ease-in-out infinite; }
        .chat-dots circle:nth-child(2) { animation-delay: .2s; }
        .chat-dots circle:nth-child(3) { animation-delay: .4s; }
        @keyframes chat-blink {
          0%, 100% { opacity: .35; transform: translateY(0); }
          50%      { opacity: 1;   transform: translateY(-1px); }
        }
      `}</style>
    </svg>
  );
}
