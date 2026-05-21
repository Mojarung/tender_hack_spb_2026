"use client";

import { motion } from "framer-motion";
import { ArrowUpRight, Sparkles } from "lucide-react";
import Link from "next/link";

import { MeshGradient } from "./shaders/MeshGradient";

const SOURCES = [
  "Wildberries", "Ozon", "Яндекс Маркет", "Megamarket", "DNS",
  "Citilink", "Re:Store", "М.Видео", "Эльдорадо", "OnlineTrade",
];

export function Hero() {
  return (
    <section className="relative">
      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
        className="relative overflow-hidden rounded-3xl p-8 md:p-12 text-white isolate"
        style={{
          // CSS-only fallback if WebGL fails to init
          background:
            "linear-gradient(135deg, #1f1147 0%, #4f46e5 45%, #c026d3 100%)",
        }}
      >
        {/* WebGL animated gradient — sits between bg and content via isolate+z. */}
        <MeshGradient
          bg={[0.10, 0.07, 0.27]}
          colorA={[0.31, 0.27, 0.92]}     /* indigo */
          colorB={[0.93, 0.30, 0.69]}     /* pink */
          colorC={[0.05, 0.04, 0.20]}     /* deep blue (darken) */
          speed={0.9}
          className="opacity-95"
        />
        {/* readability scrim */}
        <div
          aria-hidden
          className="absolute inset-0 z-[1] pointer-events-none"
          style={{
            background:
              "radial-gradient(60% 80% at 20% 30%, transparent 0%, rgba(8,5,32,0.35) 100%)",
          }}
        />

        <div className="relative z-10 grid lg:grid-cols-[1.4fr_1fr] gap-10">
          <div>
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1, duration: 0.5 }}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/10 backdrop-blur text-white/90 text-xs font-semibold border border-white/15"
            >
              <Sparkles className="w-3.5 h-3.5" />
              Парсим в реальном времени · {SOURCES.length}+ источников
            </motion.div>

            <motion.h1
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.18, duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
              className="mt-5 text-[44px] md:text-[60px] leading-[1.03] font-semibold tracking-tight"
            >
              Цены{" "}
              <span className="bg-clip-text text-transparent bg-gradient-to-r from-white via-amber-200 to-pink-200">
                всех маркетплейсов
              </span>
              <br />в одном поиске.
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.26, duration: 0.55 }}
              className="mt-4 text-white/80 text-base md:text-lg max-w-[540px]"
            >
              Ищем товар сразу на Wildberries, Ozon, Яндекс Маркете и сотнях магазинов
              Рунета. Локальный AI-ассистент сравнит цены, объяснит разницу
              и подскажет лучший вариант.
            </motion.p>

            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.34, duration: 0.55 }}
              className="mt-7 flex items-center gap-3 flex-wrap"
            >
              <Link
                href="/search?q=iphone+15+128gb"
                className="btn bg-white text-[var(--color-ink)] hover:bg-white/95 group"
              >
                Попробовать <ArrowUpRight className="w-4 h-4 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
              </Link>
              <Link
                href="/register"
                className="btn bg-white/10 text-white border border-white/20 backdrop-blur hover:bg-white/15"
              >
                Создать аккаунт
              </Link>
            </motion.div>
          </div>

          {/* Right side: glass card with live prices */}
          <motion.div
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            className="relative"
          >
            <div
              className="rounded-2xl p-5 md:p-6 border border-white/20 backdrop-blur-md text-white"
              style={{ background: "rgba(255,255,255,0.10)" }}
            >
              <div className="text-[11px] text-white/70 font-medium uppercase tracking-wider">
                live · iPhone 15 128GB
              </div>
              <div className="mt-3 space-y-2.5">
                {[
                  { label: "Wildberries", price: "53 196 ₽", color: "wb" },
                  { label: "Ozon",        price: "54 990 ₽", color: "ozon" },
                  { label: "Я.Маркет",    price: "55 490 ₽", color: "ya_market" },
                  { label: "Re:Store",    price: "52 900 ₽", color: "runet" },
                ].map((r, i) => (
                  <motion.div
                    key={r.label}
                    initial={{ opacity: 0, x: 16 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.5 + i * 0.08, duration: 0.35 }}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className="flex items-center gap-2">
                      <span className={`source-dot source-dot-${r.color}`} />
                      <span className="text-white/85">{r.label}</span>
                    </span>
                    <span className="font-semibold tabular-nums">{r.price}</span>
                  </motion.div>
                ))}
              </div>
              <div className="mt-4 pt-4 border-t border-white/15 flex items-center justify-between">
                <span className="text-xs text-white/70">Лучшая цена</span>
                <span className="text-base font-semibold text-emerald-300">52 900 ₽</span>
              </div>
            </div>

            <div
              className="mt-4 overflow-hidden h-8"
              style={{
                maskImage:
                  "linear-gradient(90deg, transparent, white 18%, white 82%, transparent)",
                WebkitMaskImage:
                  "linear-gradient(90deg, transparent, white 18%, white 82%, transparent)",
              }}
            >
              <div className="marquee-track text-[11px] text-white/65 whitespace-nowrap">
                {[...SOURCES, ...SOURCES].map((s, i) => (
                  <span key={i} className="flex items-center gap-2">
                    <span className="w-1 h-1 rounded-full bg-white/60" />
                    {s}
                  </span>
                ))}
              </div>
            </div>
          </motion.div>
        </div>
      </motion.div>
    </section>
  );
}
