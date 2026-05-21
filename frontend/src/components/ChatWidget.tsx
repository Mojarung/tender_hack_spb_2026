"use client";

import { Bot, Send, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

interface Msg { role: "user" | "assistant"; text: string; }

function sessionId(): string {
  if (typeof window === "undefined") return "anon";
  const k = "pp.chat.sid";
  let v = window.localStorage.getItem(k);
  if (!v) {
    v = `web-${crypto.randomUUID()}`;
    window.localStorage.setItem(k, v);
  }
  return v;
}

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([
    { role: "assistant", text: "Привет! Я подскажу, где купить дешевле — спросите про любой товар." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sid = useRef<string>("");
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
      setMsgs((m) => [...m, { role: "assistant", text: r.reply || "(пустой ответ от модели)" }]);
    } catch (e) {
      setMsgs((m) => [
        ...m,
        { role: "assistant", text: `⚠️ ${e instanceof Error ? e.message : "ошибка"}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-6 right-6 z-40 w-14 h-14 rounded-full bg-[var(--color-brand-500)] text-white shadow-lg shadow-[var(--color-brand-200)] hover:scale-105 transition-transform grid place-items-center"
        aria-label="Открыть чат"
      >
        {open ? <X /> : <Bot />}
      </button>

      {open && (
        <div className="fixed bottom-24 right-6 z-40 w-[360px] max-w-[calc(100vw-2rem)] h-[520px] card flex flex-col overflow-hidden">
          <div className="bg-[var(--color-brand-500)] text-white px-4 py-3 flex items-center gap-3">
            <Bot className="w-5 h-5" />
            <div className="flex-1">
              <div className="font-semibold leading-tight">PricePulse Assistant</div>
              <div className="text-xs opacity-80">Gemma 4 · локально</div>
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
            {msgs.map((m, i) => (
              <div
                key={i}
                className={
                  m.role === "user"
                    ? "ml-auto max-w-[85%] bg-[var(--color-brand-500)] text-white rounded-2xl rounded-br-sm px-3 py-2 text-sm"
                    : "max-w-[85%] bg-[var(--color-ink-50)] text-[var(--color-ink-900)] rounded-2xl rounded-bl-sm px-3 py-2 text-sm whitespace-pre-wrap"
                }
              >
                {m.text}
              </div>
            ))}
            {busy && (
              <div className="text-xs text-[var(--color-ink-400)]">⏳ модель думает…</div>
            )}
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); send(); }}
            className="border-t border-[var(--color-ink-100)] p-3 flex items-center gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Спросите про товар..."
              className="flex-1 px-3 py-2 rounded-[8px] bg-[var(--color-ink-50)] border border-transparent focus:border-[var(--color-brand-400)] focus:outline-none text-sm"
              disabled={busy}
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="btn-primary !p-2 !rounded-[8px] disabled:opacity-40"
              aria-label="Отправить"
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      )}
    </>
  );
}
