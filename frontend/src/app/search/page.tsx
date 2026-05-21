"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ProductCard } from "@/components/ProductCard";
import { api } from "@/lib/api";
import type { SearchResponse, Source } from "@/lib/types";

const SOURCE_LABEL: Record<Source, string> = {
  wb: "Wildberries",
  ozon: "Ozon",
  ya_market: "Яндекс Маркет",
  runet: "Рунет",
};

export default function SearchPage() {
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filterSource, setFilterSource] = useState<Source | "all">("all");

  useEffect(() => {
    if (!q) return;
    setData(null);
    setErr(null);
    api.search(q, 12)
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : "ошибка"));
  }, [q]);

  if (!q) return <div className="text-[var(--color-ink-500)]">Введите запрос в поиск выше.</div>;

  const offers = data?.groups?.flatMap((g) => g.offers) ?? [];
  const filtered = filterSource === "all" ? offers : offers.filter((o) => o.source === filterSource);

  return (
    <div className="flex flex-col lg:flex-row gap-6">
      <aside className="lg:w-[260px] shrink-0">
        <div className="card p-5">
          <div className="text-xs uppercase tracking-wider text-[var(--color-ink-400)] mb-3">Источник</div>
          <ul className="space-y-2 text-sm">
            <SourcePill checked={filterSource === "all"} onClick={() => setFilterSource("all")} label="Все" count={offers.length} />
            {data?.groups?.map((g) => (
              <SourcePill
                key={g.source}
                checked={filterSource === g.source}
                onClick={() => setFilterSource(g.source)}
                label={SOURCE_LABEL[g.source]}
                count={g.count}
                error={g.error}
              />
            ))}
          </ul>

          {data?.top_deals?.length ? (
            <>
              <div className="text-xs uppercase tracking-wider text-[var(--color-ink-400)] mt-6 mb-3">Лучшие сделки</div>
              <ol className="space-y-2">
                {data.top_deals.slice(0, 3).map((d) => (
                  <li key={d.rank} className="flex items-center gap-2 text-sm">
                    <span className="w-6 h-6 rounded-full bg-[var(--color-brand-50)] text-[var(--color-brand-500)] grid place-items-center text-xs font-bold">
                      {d.rank}
                    </span>
                    <span className="flex-1 truncate text-[var(--color-ink-700)]">{d.offer.name}</span>
                  </li>
                ))}
              </ol>
            </>
          ) : null}
        </div>
      </aside>

      <div className="flex-1">
        <h1 className="text-2xl font-semibold mb-1">Результаты по «{q}»</h1>
        <p className="text-sm text-[var(--color-ink-500)]">
          Найдено {offers.length} предложений
          {data?.took_ms != null && ` · ${data.took_ms} мс`}
        </p>

        {err && (
          <div className="mt-4 p-4 rounded-md bg-red-50 text-red-700 text-sm">
            {err}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 mt-6">
          {filtered.map((o, i) => (
            <ProductCard key={`${o.source}-${i}`} offer={o} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SourcePill({
  checked, onClick, label, count, error,
}: { checked: boolean; onClick: () => void; label: string; count: number; error?: string | null }) {
  return (
    <li>
      <button
        onClick={onClick}
        className={
          "w-full flex items-center justify-between gap-3 px-3 py-2 rounded-md transition-colors " +
          (checked ? "bg-[var(--color-brand-50)] text-[var(--color-brand-700)]"
                   : "hover:bg-[var(--color-ink-50)] text-[var(--color-ink-700)]")
        }
      >
        <span className="flex items-center gap-2">
          <span className={checked ? "w-2 h-2 rounded-full bg-[var(--color-brand-500)]" : "w-2 h-2 rounded-full bg-[var(--color-ink-200)]"} />
          {label}
        </span>
        <span className="text-xs text-[var(--color-ink-400)]">
          {error ? "⚠" : count}
        </span>
      </button>
    </li>
  );
}
