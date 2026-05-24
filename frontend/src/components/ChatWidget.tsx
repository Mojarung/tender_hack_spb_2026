"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ExternalLink, RotateCcw, Search, Send, Sparkles, Wrench, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

import { ChatBubbleIcon } from "./ChatBubbleIcon";

interface ToolTrace { name: string; result_keys: unknown }
interface Msg { role: "user" | "assistant"; text: string; tool_calls?: ToolTrace[] }

const HISTORY_KEY = "pp.chat.history.v2";
const SID_KEY = "pp.chat.sid";
const HISTORY_TTL_MS = 24 * 3600 * 1000;
const HISTORY_MAX = 40;    // last N messages kept in localStorage

const GREETING: Msg = {
  role: "assistant",
  text:
    "Привет! Я **AI-ассистент PricePulse** на локальной Gemma. Умею искать товары, "
    + "сравнивать цены и подбирать оптимальный вариант по требованиям. Спроси что-нибудь "
    + "или нажми на подсказку ниже.",
};

const QUICK_PROMPTS = [
  "Найди ноутбук до 60 тысяч с SSD",
  "Сравни цены на iPhone 15 128GB",
  "Какие сейчас лучшие предложения по шинам Michelin 205/55 R16?",
  "Покажи топ-3 принтера лазерных для офиса до 25 тысяч",
];

function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map((b, i) =>
      [4, 6, 8, 10].includes(i) ? `-${b.toString(16).padStart(2, "0")}` : b.toString(16).padStart(2, "0"),
    ).join("");
}

function sessionId(): string {
  if (typeof window === "undefined") return "anon";
  let v = window.localStorage.getItem(SID_KEY);
  if (!v) {
    v = `web-${uuid()}`;
    window.localStorage.setItem(SID_KEY, v);
  }
  return v;
}

function loadHistory(): Msg[] {
  if (typeof window === "undefined") return [GREETING];
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    if (!raw) return [GREETING];
    const { ts, msgs } = JSON.parse(raw) as { ts: number; msgs: Msg[] };
    if (Date.now() - ts > HISTORY_TTL_MS || !Array.isArray(msgs) || msgs.length === 0) {
      return [GREETING];
    }
    return msgs;
  } catch {
    return [GREETING];
  }
}

function saveHistory(msgs: Msg[]) {
  if (typeof window === "undefined") return;
  try {
    const trimmed = msgs.length > HISTORY_MAX ? msgs.slice(-HISTORY_MAX) : msgs;
    window.localStorage.setItem(
      HISTORY_KEY, JSON.stringify({ ts: Date.now(), msgs: trimmed }),
    );
  } catch { /* quota or disabled storage — silently skip */ }
}

/** Minimal markdown — bold, [text](url), and newlines. We don't pull
 *  react-markdown for a chat bubble; this covers what Gemma actually emits. */
function renderMarkdown(text: string): React.ReactNode {
  // Split on inline tokens while keeping the delimiters.
  const tokens = text.split(/(\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\))/g);
  return tokens.map((tok, i) => {
    if (/^\*\*[^*]+\*\*$/.test(tok)) {
      return <strong key={i}>{tok.slice(2, -2)}</strong>;
    }
    const link = tok.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    if (link) {
      return (
        <a
          key={i}
          href={link[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[var(--color-accent)] underline decoration-dotted hover:decoration-solid inline-flex items-center gap-0.5"
        >
          {link[1]}
          <ExternalLink className="w-3 h-3" />
        </a>
      );
    }
    return <span key={i}>{tok}</span>;
  });
}

/** Pull a tool argument off our condensed trace. Backend stores
 *  `result_keys` only — but if the tool was `search_products` we know
 *  the user asked it about a query → expose a quick "Open results" link.
 *  We can't recover the query from `result_keys` alone, so we lean on the
 *  preceding user message for that. */
function findSearchQuery(msgs: Msg[], idx: number): string | null {
  if (idx <= 0) return null;
  for (let j = idx - 1; j >= 0; j--) {
    if (msgs[j].role === "user") return msgs[j].text;
  }
  return null;
}


export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sid = useRef("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Hydrate once on mount — must happen client-side to avoid SSR mismatch.
  useEffect(() => {
    sid.current = sessionId();
    setMsgs(loadHistory());
  }, []);

  // Persist + autoscroll on every msgs change.
  useEffect(() => { saveHistory(msgs); }, [msgs]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 99999, behavior: "smooth" });
  }, [msgs, open, busy]);

  // Focus input when the widget opens.
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 220); }, [open]);

  async function send(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const r = await api.chat(text, sid.current);
      setMsgs((m) => [...m, {
        role: "assistant",
        text: r.reply || "_(пустой ответ модели)_",
        tool_calls: r.tool_calls,
      }]);
    } catch (e) {
      setMsgs((m) => [...m, {
        role: "assistant",
        text: `⚠️ ${e instanceof Error ? e.message.slice(0, 200) : "ошибка"}`,
      }]);
    } finally { setBusy(false); }
  }

  function resetChat() {
    // New session id on full reset — server forgets old context too.
    const fresh = `web-${uuid()}`;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(SID_KEY, fresh);
    }
    sid.current = fresh;
    setMsgs([GREETING]);
  }

  const isEmpty = msgs.length === 1 && msgs[0].role === "assistant";

  return (
    <>
      <motion.button
        whileHover={{ scale: 1.04 }}
        whileTap={{ scale: 0.96 }}
        onClick={() => setOpen(!open)}
        className="fixed bottom-6 right-6 z-40 w-12 h-12 rounded-full bg-[var(--color-ink)] text-white grid place-items-center"
        aria-label="Открыть чат"
        style={{ boxShadow: "0 12px 32px rgba(11,13,18,0.25)" }}
      >
        <AnimatePresence mode="wait" initial={false}>
          {open ? (
            <motion.span key="x" initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} transition={{ duration: 0.2 }}>
              <X className="w-5 h-5" />
            </motion.span>
          ) : (
            <motion.span key="b" initial={{ rotate: 90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: -90, opacity: 0 }} transition={{ duration: 0.2 }}>
              <ChatBubbleIcon size={22} />
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 18, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.96 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="fixed bottom-24 right-6 z-40 w-[420px] max-w-[calc(100vw-2rem)] h-[620px] max-h-[calc(100vh-7rem)] card flex flex-col overflow-hidden"
            style={{ boxShadow: "0 24px 64px rgba(11,13,18,0.18)" }}
          >
            <div className="px-4 py-3 flex items-center gap-3 border-b border-[var(--color-border)]">
              <div className="w-8 h-8 rounded-full bg-[var(--color-accent-50)] grid place-items-center">
                <Sparkles className="w-4 h-4 text-[var(--color-accent)]" />
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold leading-tight">PricePulse Assistant</div>
                <div className="text-[11px] text-[var(--color-ink-4)]">Gemma · локально · с поиском</div>
              </div>
              <button
                type="button"
                onClick={resetChat}
                disabled={busy || isEmpty}
                title="Очистить переписку"
                aria-label="Очистить переписку"
                className="p-1.5 rounded-full hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <RotateCcw className="w-3.5 h-3.5 stroke-[var(--color-ink-3)]" />
              </button>
            </div>

            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
              {msgs.map((m, i) => {
                const isUser = m.role === "user";
                const usedTools = (m.tool_calls ?? []).filter((t) => t.name);
                const usedSearch = usedTools.some((t) => t.name === "search_products");
                const searchQuery = usedSearch ? findSearchQuery(msgs, i) : null;
                return (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.2 }}
                    className={isUser ? "flex justify-end" : ""}
                  >
                    <div
                      className={
                        isUser
                          ? "max-w-[85%] bg-[var(--color-ink)] text-white rounded-2xl rounded-br-sm px-3 py-2 text-sm whitespace-pre-wrap break-words"
                          : "max-w-[90%] bg-[var(--color-surface-2)] text-[var(--color-ink)] rounded-2xl rounded-bl-sm px-3 py-2 text-sm whitespace-pre-wrap break-words"
                      }
                    >
                      {isUser ? m.text : renderMarkdown(m.text)}
                      {usedTools.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-[var(--color-border)] flex flex-wrap gap-1.5">
                          {usedTools.map((t, j) => (
                            <span
                              key={`${t.name}-${j}`}
                              className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-white/60 text-[var(--color-ink-3)] border border-[var(--color-border)]"
                              title={`Инструмент: ${t.name}`}
                            >
                              <Wrench className="w-2.5 h-2.5" />
                              {t.name}
                            </span>
                          ))}
                          {searchQuery && (
                            <Link
                              href={`/search?q=${encodeURIComponent(searchQuery)}`}
                              onClick={() => setOpen(false)}
                              className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-2)] transition-colors"
                            >
                              <Search className="w-2.5 h-2.5" />
                              Открыть карточки
                            </Link>
                          )}
                        </div>
                      )}
                    </div>
                  </motion.div>
                );
              })}
              {busy && (
                <div className="flex items-center gap-2 text-xs text-[var(--color-ink-4)] italic">
                  <span className="inline-flex gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-ink-4)] animate-bounce" style={{ animationDelay: "0ms" }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-ink-4)] animate-bounce" style={{ animationDelay: "120ms" }} />
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-ink-4)] animate-bounce" style={{ animationDelay: "240ms" }} />
                  </span>
                  модель ищет и думает…
                </div>
              )}
              {isEmpty && !busy && (
                <div className="pt-2">
                  <div className="text-[11px] text-[var(--color-ink-4)] mb-2 px-1">Попробуйте:</div>
                  <div className="flex flex-col gap-1.5">
                    {QUICK_PROMPTS.map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => send(p)}
                        className="text-left text-xs px-3 py-2 rounded-lg bg-[var(--color-surface-2)] hover:bg-[var(--color-accent-50)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] transition-colors border border-[var(--color-border)]"
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <form
              onSubmit={(e) => { e.preventDefault(); send(); }}
              className="border-t border-[var(--color-border)] p-3 flex items-center gap-2"
            >
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Спросите про товар…"
                className="input !py-2 !text-sm !rounded-full"
                disabled={busy}
              />
              <button
                type="submit"
                disabled={busy || !input.trim()}
                className="btn btn-primary !p-2.5 !rounded-full disabled:opacity-40"
                aria-label="Отправить"
              >
                <Send className="w-4 h-4" />
              </button>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
