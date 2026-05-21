"use client";

import { motion } from "framer-motion";
import { ArrowUpRight, Sparkles } from "lucide-react";
import Link from "next/link";

const SOURCES = [
  "Wildberries", "Ozon", "Яндекс Маркет", "Megamarket", "DNS",
  "Citilink", "Re:Store", "М.Видео", "Эльдорадо", "Ситилинк",
];

export function Hero() {
  return (
    <section className="relative">
      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
        className="card card-hover relative overflow-hidden p-8 md:p-12"
      >
        {/* Subtle dotted background */}
        <div
          aria-hidden
          className="absolute inset-0 opacity-[0.4] pointer-events-none"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, var(--color-line-2) 1px, transparent 0)",
            backgroundSize: "22px 22px",
            maskImage: "radial-gradient(ellipse at top, black 30%, transparent 70%)",
          }}
        />

        <div className="relative z-10 grid lg:grid-cols-[1.4fr_1fr] gap-10">
          <div>
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1, duration: 0.5 }}
              className="inline-flex items-center gap-1.5 chip"
            >
              <Sparkles className="w-3.5 h-3.5 text-[var(--color-accent)]" />
              Парсим в реальном времени · {SOURCES.length}+ источников
            </motion.div>

            <motion.h1
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.18, duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
              className="mt-5 text-[44px] md:text-[56px] leading-[1.05] font-semibold tracking-tight"
            >
              Цены{" "}
              <span className="text-[var(--color-accent)]">всех маркетплейсов</span>
              <br />в одном поиске.
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.26, duration: 0.55 }}
              className="mt-4 text-[var(--color-ink-3)] text-base md:text-lg max-w-[540px]"
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
              <Link href="/search?q=iphone+15+128gb" className="btn btn-primary group">
                Попробовать <ArrowUpRight className="w-4 h-4 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
              </Link>
              <Link href="/register" className="btn btn-ghost">
                Создать аккаунт
              </Link>
            </motion.div>
          </div>

          {/* Right side: scrolling marquee of sources + price preview */}
          <motion.div
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            className="relative"
          >
            <div className="card !rounded-2xl p-5 md:p-6">
              <div className="text-xs text-[var(--color-ink-4)] font-medium uppercase tracking-wider">
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
                      <span className="text-[var(--color-ink-2)]">{r.label}</span>
                    </span>
                    <span className="font-semibold tabular-nums">{r.price}</span>
                  </motion.div>
                ))}
              </div>
              <div className="mt-4 pt-4 border-t border-[var(--color-line)] flex items-center justify-between">
                <span className="text-xs text-[var(--color-ink-4)]">Лучшая цена</span>
                <span className="text-base font-semibold text-[var(--color-good)]">52 900 ₽</span>
              </div>
            </div>

            <div className="mt-4 overflow-hidden h-8 mask-fade-x">
              <div className="marquee-track text-xs text-[var(--color-ink-4)] whitespace-nowrap">
                {[...SOURCES, ...SOURCES].map((s, i) => (
                  <span key={i} className="flex items-center gap-2">
                    <span className="w-1 h-1 rounded-full bg-[var(--color-ink-4)]" />
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
