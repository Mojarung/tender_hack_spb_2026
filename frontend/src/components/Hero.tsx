"use client";

import { motion } from "framer-motion";
import { ArrowUpRight, Search, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

const SOURCES = ["Wildberries", "Ozon", "Я.Маркет", "Рунет"];

export function Hero() {
  const router = useRouter();
  const [q, setQ] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = q.trim();
    if (!t) return;
    router.push(`/search?q=${encodeURIComponent(t)}`);
  }

  return (
    <section className="relative pt-12 md:pt-20 pb-12 md:pb-16 text-center">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/80 backdrop-blur text-[var(--color-ink-3)] text-xs font-semibold border border-[var(--color-line)]"
      >
        <Sparkles className="w-3.5 h-3.5 text-[var(--color-accent)]" />
        {SOURCES.length}+ источников · бесплатно
      </motion.div>

      <motion.h1
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.08, duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
        className="mt-6 text-[44px] md:text-[68px] leading-[1.04] font-semibold tracking-tight max-w-[920px] mx-auto"
      >
        Цены{" "}
        <span className="bg-clip-text text-transparent bg-gradient-to-r from-[var(--color-accent)] via-fuchsia-500 to-rose-500">
          всех маркетплейсов
        </span>
        <br />в одном поиске
      </motion.h1>

      <motion.p
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.18, duration: 0.55 }}
        className="mt-5 text-[var(--color-ink-3)] text-base md:text-lg max-w-[640px] mx-auto"
      >
        Wildberries, Ozon, Яндекс Маркет и сотни магазинов Рунета — за один запрос.
        Локальный AI-ассистент сравнит цены, отзывы и историю.
      </motion.p>

      <motion.form
        onSubmit={submit}
        initial={{ opacity: 0, y: 12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ delay: 0.28, duration: 0.5 }}
        className="mt-9 max-w-[560px] mx-auto"
      >
        <div className="relative">
          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-5 h-5 text-[var(--color-ink-4)]" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="iPhone 15 128, кофемашина, кроссовки adidas…"
            className="w-full pl-14 pr-36 py-4 text-base rounded-full bg-white border border-[var(--color-line)] shadow-[0_8px_24px_rgba(11,13,18,0.06)] focus:outline-none focus:border-[var(--color-accent)] focus:shadow-[0_8px_24px_rgba(79,70,229,0.18)] transition-all"
            autoFocus
          />
          <button
            type="submit"
            disabled={!q.trim()}
            className="absolute right-1.5 top-1/2 -translate-y-1/2 btn btn-primary rounded-full !py-2.5 disabled:opacity-50"
          >
            Найти <ArrowUpRight className="w-4 h-4" />
          </button>
        </div>
      </motion.form>

    </section>
  );
}
