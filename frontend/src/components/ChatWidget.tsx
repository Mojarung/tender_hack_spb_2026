"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Send, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { ChatBubbleIcon } from "./ChatBubbleIcon";

interface Msg { role: "user" | "assistant"; text: string; }

function sessionId(): string {
  if (typeof window === "undefined") return "anon";
  const k = "pp.chat.sid";
  let v = window.localStorage.getItem(k);
  if (!v) {
    const id = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16))).map((b, i) =>
          [4, 6, 8, 10].includes(i) ? `-${b.toString(16).padStart(2, "0")}` : b.toString(16).padStart(2, "0")
        ).join("");
    v = `web-${id}`;
    window.localStorage.setItem(k, v);
  }
  return v;
}

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: "assistant", text: "Привет! Я локальный AI на Gemma 4 — спросите про любой товар, найду лучшую цену." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sid = useRef("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { sid.current = sessionId(); }, []);
  useEffect(() => { scrollRef.current?.scrollTo({ top: 99999, behavior: "smooth" }); }, [msgs, open]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const r = await api.chat(text, sid.current);
      setMsgs((m) => [...m, { role: "assistant", text: r.reply || "(пустой ответ модели)" }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: `⚠️ ${e instanceof Error ? e.message.slice(0, 200) : "ошибка"}` }]);
    } finally { setBusy(false); }
  }

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
            className="fixed bottom-24 right-6 z-40 w-[380px] max-w-[calc(100vw-2rem)] h-[540px] card flex flex-col overflow-hidden"
            style={{ boxShadow: "0 24px 64px rgba(11,13,18,0.18)" }}
          >
            <div className="px-4 py-3 flex items-center gap-3 border-b border-[var(--color-line)]">
              <div className="w-8 h-8 rounded-full bg-[var(--color-accent-50)] grid place-items-center">
                <Sparkles className="w-4 h-4 text-[var(--color-accent)]" />
              </div>
              <div className="flex-1">
                <div className="text-sm font-semibold leading-tight">PricePulse Assistant</div>
                <div className="text-[11px] text-[var(--color-ink-4)]">Gemma 4 · локально</div>
              </div>
            </div>

            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2.5">
              {msgs.map((m, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2 }}
                  className={
                    m.role === "user"
                      ? "ml-auto max-w-[85%] bg-[var(--color-ink)] text-white rounded-2xl rounded-br-sm px-3 py-2 text-sm"
                      : "max-w-[85%] bg-[var(--color-surface-2)] text-[var(--color-ink)] rounded-2xl rounded-bl-sm px-3 py-2 text-sm whitespace-pre-wrap"
                  }
                >
                  {m.text}
                </motion.div>
              ))}
              {busy && (
                <div className="flex items-center gap-2 text-xs text-[var(--color-ink-4)]">
                  <span className="spinner" /> модель думает…
                </div>
              )}
            </div>

            <form
              onSubmit={(e) => { e.preventDefault(); send(); }}
              className="border-t border-[var(--color-line)] p-3 flex items-center gap-2"
            >
              <input
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