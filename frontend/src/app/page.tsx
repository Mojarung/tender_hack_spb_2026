"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";

import { Hero } from "@/components/Hero";
import { ProductCard } from "@/components/ProductCard";
import { GridSkeleton } from "@/components/Skeleton";
import { api } from "@/lib/api";
import { MOCK_OFFERS, MOCK_TOP_DEALS } from "@/lib/mock";
import type { ProductOffer, RankedOffer, SearchResponse } from "@/lib/types";

const PROMOTED: { title: string; q: string }[] = [
  { title: "iPhone 15 128GB", q: "iphone 15 128gb" },
  { title: "MacBook Air M3",  q: "macbook air m3" },
  { title: "Sony WH-1000XM5", q: "sony wh-1000xm5" },
  { title: "Робот-пылесос",   q: "робот пылесос" },
];

export default function Home() {
  const [query, setQuery] = useState(PROMOTED[0].q);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null); setErr(null); setLoading(true);
    api.search(query, 6)
      .then((r) => { if (!cancelled) { setData(r); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setErr(String(e?.message ?? e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [query]);

  const top: RankedOffer[] = (data?.top_deals?.length ?? 0) > 0
    ? (data!.top_deals).slice(0, 4)
    : MOCK_TOP_DEALS;

  const live: ProductOffer[] = data?.groups?.flatMap((g) => g.offers) ?? [];
  const recs: ProductOffer[] = live.length > 0 ? live.slice(0, 8) : MOCK_OFFERS;
  const usingMock = (data?.top_deals?.length ?? 0) === 0;

  return (
    <div className="space-y-12">
      <Hero />

      <section>
        <SectionHeader
          title="Топ-предложения сегодня"
          subtitle={
            loading
              ? "Идёт поиск…"
              : usingMock
                ? "Демо-данные — бэкенд молчит или временно ограничен"
                : `Best-Deal Score · ответ за ${data?.took_ms} мс`
          }
        />
        {loading ? (
          <GridSkeleton count={4} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {top.map((d, i) => (
              <ProductCard key={`${d.offer.source}-${d.rank}-${i}`} offer={d.offer} index={i} highlight={i === 0} />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionHeader title="Подборки" subtitle="Один клик — мгновенный поиск" />
        <div className="flex gap-2 mt-3 flex-wrap">
          {PROMOTED.map((p) => (
            <motion.button
              key={p.q}
              whileTap={{ scale: 0.96 }}
              onClick={() => setQuery(p.q)}
              className={p.q === query ? "chip chip-active" : "chip"}
            >
              {p.title}
            </motion.button>
          ))}
        </div>

        {err && (
          <div className="mt-4 p-3 rounded-xl bg-amber-50 text-amber-800 text-sm border border-amber-200">
            Бэкенд недоступен ({err}). Показываю демо-данные.
          </div>
        )}

        <div className="mt-6">
          {loading ? (
            <GridSkeleton count={8} />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {recs.map((o, i) => (
                <ProductCard key={`${o.source}-${o.name}-${i}`} offer={o} index={i} />
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div>
        <h2 className="text-xl md:text-2xl font-semibold tracking-tight">{title}</h2>
        {subtitle && (
          <p className="text-sm text-[var(--color-ink-4)] mt-1">{subtitle}</p>
        )}
      </div>
      <Link href="/search?q=iphone+15+128gb" className="text-sm text-[var(--color-ink-3)] hover:text-[var(--color-ink)] inline-flex items-center gap-1 transition-colors">
        Все <ArrowRight className="w-3.5 h-3.5" />
      </Link>
    </div>
  );
}
