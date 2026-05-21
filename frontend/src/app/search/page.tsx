"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CornerDownLeft, SparkleIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ProductCard } from "@/components/ProductCard";
import { GridSkeleton } from "@/components/Skeleton";
import { api } from "@/lib/api";
import { SOURCE_LABEL, type SearchResponse, type Source } from "@/lib/types";

export const dynamic = "force-dynamic";

function SearchInner() {
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const nofix = params.get("nofix") === "1";
  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Source | "all">("all");

  useEffect(() => {
    if (!q) { setLoading(false); return; }
    let cancelled = false;
    setData(null); setErr(null); setLoading(true);
    api.search(q, 16, { nofix })
      .then((r) => { if (!cancelled) { setData(r); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setErr(String(e?.message ?? e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [q, nofix]);

  if (!q) {
    return (
      <div className="card p-12 text-center">
        <h1 className="text-xl font-semibold">Введите запрос</h1>
        <p className="text-sm text-[var(--color-ink-4)] mt-1">
          Используйте поиск в шапке.
        </p>
      </div>
    );
  }

  const all = data?.groups?.flatMap((g) => g.offers) ?? [];
  const offers = filter === "all" ? all : all.filter((o) => o.source === filter);
  const empty = !loading && all.length === 0;

  return (
    <div className="flex flex-col lg:flex-row gap-6">
      <aside className="lg:w-[260px] shrink-0">
        <div className="card p-5 sticky top-20">
          <div className="text-xs uppercase tracking-wider text-[var(--color-ink-4)] font-medium mb-3">Источник</div>
          <ul className="space-y-1 text-sm">
            <Pill checked={filter === "all"} onClick={() => setFilter("all")} label="Все источники" count={all.length} />
            {data?.groups?.map((g) => (
              <Pill key={g.source} checked={filter === g.source}
                onClick={() => setFilter(g.source)}
                label={SOURCE_LABEL[g.source]} count={g.count} error={g.error} />
            ))}
          </ul>

          {data?.top_deals?.length ? (
            <>
              <div className="text-xs uppercase tracking-wider text-[var(--color-ink-4)] font-medium mt-6 mb-3">Лучшие сделки</div>
              <ol className="space-y-1.5">
                {data.top_deals.slice(0, 3).map((d) => (
                  <li key={d.rank} className="flex items-center gap-2 text-sm">
                    <span className="w-6 h-6 rounded-full bg-[var(--color-accent-50)] text-[var(--color-accent-2)] grid place-items-center text-[11px] font-bold">{d.rank}</span>
                    <span className="flex-1 truncate text-[var(--color-ink-2)]">{d.offer.name}</span>
                  </li>
                ))}
              </ol>
            </>
          ) : null}

          {data?.took_ms != null && (
            <div className="mt-6 pt-4 border-t border-[var(--color-line)] text-xs text-[var(--color-ink-4)]">
              Ответ за <span className="text-[var(--color-ink-2)] font-semibold tabular-nums">{data.took_ms} мс</span>
            </div>
          )}
        </div>
      </aside>

      <div className="flex-1 min-w-0">
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          <h1 className="text-2xl font-semibold tracking-tight">Результаты по «{q}»</h1>
          <p className="text-sm text-[var(--color-ink-4)] mt-1">
            {loading ? "Идёт поиск…" : `Найдено ${all.length} предложений`}
          </p>
        </motion.div>

        <CorrectionBanner data={data} rawQuery={q} nofix={nofix} />

        {err && (
          <div className="mt-4 p-3 rounded-xl bg-amber-50 text-amber-800 text-sm border border-amber-200">
            Бэкенд: {err}
          </div>
        )}

        {loading ? (
          <div className="mt-6"><GridSkeleton count={6} /></div>
        ) : empty ? (
          <div className="mt-6 card p-12 text-center">
            <div className="text-3xl">🔎</div>
            <p className="mt-3 text-[var(--color-ink-2)] font-semibold">Ничего не нашли</p>
            <p className="text-sm text-[var(--color-ink-4)] mt-1">
              Источники могли временно заблокировать запрос. Попробуйте чуть позже или измените формулировку.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 mt-6">
            {offers.map((o, i) => (
              <ProductCard key={`${o.source}-${o.name}-${i}`} offer={o} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<div className="text-[var(--color-ink-4)] text-sm">Загружаю…</div>}>
      <SearchInner />
    </Suspense>
  );
}

function CorrectionBanner({
  data, rawQuery, nofix,
}: { data: SearchResponse | null; rawQuery: string; nofix: boolean }) {
  // Show only when the backend actually rewrote the query.
  const normalized = data?.query?.normalized ?? "";
  const cleanedRaw = rawQuery.trim().toLowerCase();
  const changed = !!normalized && normalized !== cleanedRaw;
  const expansions = data?.query?.expansions ?? [];

  return (
    <AnimatePresence initial={false}>
      {nofix ? (
        <motion.div
          key="reverted"
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          className="mt-4 p-3 rounded-xl bg-[var(--color-surface-2)] text-sm flex items-center gap-2 text-[var(--color-ink-3)] border border-[var(--color-line)]"
        >
          <CornerDownLeft className="w-4 h-4 text-[var(--color-ink-4)]" />
          Ищем точно как написано — без исправлений.
          <Link
            href={`/search?q=${encodeURIComponent(rawQuery)}`}
            className="ml-auto text-[var(--color-accent)] hover:underline font-medium"
          >
            Включить исправления
          </Link>
        </motion.div>
      ) : changed && (
        <motion.div
          key="fixed"
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          className="mt-4 p-3 rounded-xl bg-[var(--color-accent-50)] border border-[var(--color-accent-100)] text-sm flex flex-wrap items-center gap-x-2 gap-y-1"
        >
          <SparkleIcon className="w-4 h-4 text-[var(--color-accent)]" />
          <span className="text-[var(--color-ink-2)]">
            Исправили на{" "}
            <span className="font-semibold text-[var(--color-ink)]">«{normalized}»</span>
          </span>
          {expansions.length > 0 && (
            <span className="text-[var(--color-ink-4)] text-xs">
              · {expansions.join(" · ")}
            </span>
          )}
          <Link
            href={`/search?q=${encodeURIComponent(rawQuery)}&nofix=1`}
            className="ml-auto text-[var(--color-accent)] hover:underline font-medium"
          >
            Искать «{rawQuery}»
          </Link>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Pill({
  checked, onClick, label, count, error,
}: { checked: boolean; onClick: () => void; label: string; count: number; error?: string | null }) {
  return (
    <li>
      <button
        onClick={onClick}
        className={
          "w-full flex items-center justify-between gap-3 px-3 py-2 rounded-lg text-left transition-colors " +
          (checked ? "bg-[var(--color-ink)] text-white"
                   : "hover:bg-[var(--color-surface-2)] text-[var(--color-ink-2)]")
        }
      >
        <span>{label}</span>
        <span className={checked ? "text-white/80 text-xs" : "text-xs text-[var(--color-ink-4)]"}>
          {error ? "⚠" : count}
        </span>
      </button>
    </li>
  );
}
