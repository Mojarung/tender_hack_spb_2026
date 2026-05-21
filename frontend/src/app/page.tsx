"use client";

import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";

import { Hero } from "@/components/Hero";
import { ProductCard } from "@/components/ProductCard";
import { api } from "@/lib/api";
import { MOCK_OFFERS, MOCK_TOP_DEALS } from "@/lib/mock";
import type { ProductOffer, RankedOffer, SearchResponse } from "@/lib/types";

const DEMO_QUERIES = [
  "iphone 15 128gb",
  "macbook air m3",
  "robot vacuum",
  "беспроводные наушники",
];

export default function Home() {
  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState(DEMO_QUERIES[0]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(null);
    setLoading(true);
    api.search(query, 4)
      .then((r) => { if (!cancelled) { setData(r); setLoading(false); } })
      .catch((e) => {
        if (cancelled) return;
        setErr(e instanceof Error ? e.message : "ошибка");
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [query]);

  // Merge live data with mock fallback so the layout reads correctly
  // even before the backend resolves (or when sources are blocked).
  const topDeals: RankedOffer[] = (data?.top_deals && data.top_deals.length > 0)
    ? data.top_deals
    : MOCK_TOP_DEALS;
  const liveOffers = data?.groups?.flatMap((g) => g.offers) ?? [];
  const recOffers: ProductOffer[] = liveOffers.length > 0 ? liveOffers : MOCK_OFFERS;

  return (
    <div className="space-y-12">
      <Hero />

      <section>
        <SectionHeader
          title="Топ-предложения сегодня"
          subtitle={
            data?.top_deals?.length
              ? `Best-Deal Score · ответ за ${data.took_ms} мс`
              : loading
                ? "Загружаю реальную выдачу…"
                : "Демо-данные (бэкенд молчит — показываю layout)"
          }
        />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mt-6">
          {topDeals.slice(0, 4).map((d) => (
            <ProductCard key={`${d.offer.source}-${d.rank}`} offer={d.offer} />
          ))}
        </div>
      </section>

      <section>
        <SectionHeader
          title="Рекомендации"
          subtitle={`Выдача по запросу «${query}»`}
        />
        <div className="flex gap-2 mt-3 flex-wrap">
          {DEMO_QUERIES.map((q) => (
            <button
              key={q}
              onClick={() => setQuery(q)}
              className={
                q === query
                  ? "px-3 py-1.5 rounded-full bg-[var(--color-brand-500)] text-white text-sm"
                  : "px-3 py-1.5 rounded-full bg-white border border-[var(--color-ink-200)] text-sm hover:border-[var(--color-brand-400)]"
              }
            >
              {q}
            </button>
          ))}
        </div>

        {err && (
          <div className="mt-4 p-4 rounded-md bg-amber-50 text-amber-800 text-sm">
            Бэкенд молчит ({err}). Показываю demo-данные.
          </div>
        )}

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mt-6">
          {recOffers.slice(0, 8).map((o, i) => (
            <ProductCard key={`${o.source}-${i}-${o.name}`} offer={o} />
          ))}
        </div>
      </section>
    </div>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="flex items-end justify-between">
      <div>
        <h2 className="text-xl font-semibold text-[var(--color-ink-900)]">{title}</h2>
        {subtitle && <p className="text-sm text-[var(--color-ink-400)] mt-1">{subtitle}</p>}
      </div>
      <a className="text-sm text-[var(--color-brand-500)] flex items-center gap-1 hover:underline" href="/search">
        Все <ArrowRight className="w-4 h-4" />
      </a>
    </div>
  );
}
