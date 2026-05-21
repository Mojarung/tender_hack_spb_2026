"use client";

import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";

import { Hero } from "@/components/Hero";
import { ProductCard } from "@/components/ProductCard";
import { api } from "@/lib/api";
import type { ProductOffer, SearchResponse } from "@/lib/types";

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

  useEffect(() => {
    setData(null);
    setErr(null);
    api.search(query, 4)
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : "ошибка загрузки"));
  }, [query]);

  return (
    <div className="space-y-12">
      <Hero />

      <section>
        <SectionHeader title="Топ-предложения сегодня" subtitle="Best-Deal Score across all sources" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mt-6">
          {data?.top_deals?.slice(0, 4).map((d) => (
            <ProductCard key={`${d.offer.source}-${d.rank}`} offer={d.offer} />
          )) ?? <Skeleton count={4} />}
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
          <div className="mt-4 p-4 rounded-md bg-red-50 text-red-700 text-sm">
            Backend недоступен: {err}. Проверьте что <code>uvicorn pricepulse.main:app</code> запущен на :8000.
          </div>
        )}

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 mt-6">
          {data?.groups?.flatMap((g) => g.offers).slice(0, 8).map((o, i) => (
            <ProductCard key={`${o.source}-${i}`} offer={o} />
          )) ?? <Skeleton count={8} />}
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

function Skeleton({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="card p-5 h-[280px] animate-pulse">
          <div className="h-4 w-20 bg-[var(--color-ink-100)] rounded mb-3" />
          <div className="h-5 w-3/4 bg-[var(--color-ink-100)] rounded mb-2" />
          <div className="h-32 bg-[var(--color-ink-50)] rounded mt-3" />
        </div>
      ))}
    </>
  );
}

const _offer: ProductOffer | undefined = undefined; // keep type referenced
void _offer;
