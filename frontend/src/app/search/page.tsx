"use client";

import { motion } from "framer-motion";
import { CornerDownLeft, SearchX } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { ProductCard } from "@/components/ProductCard";
import { GridSkeleton } from "@/components/Skeleton";
import { api } from "@/lib/api";
import { SOURCE_LABEL, type SearchResponse, type Source } from "@/lib/types";

export const dynamic = "force-dynamic";

function SearchInner() {
  const router = useRouter();
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const from = (params.get("from") ?? "").trim();    // original user query (after a fix)
  const nofix = params.get("nofix") === "1";
  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Source | "all">("all");

  /** When we replace the URL to the corrected query, the effect re-fires
   *  for the new `q`. The next run reuses the data already in state and
   *  just clears the loading flag — no duplicate request. */
  const skipNextFetch = useRef(false);

  useEffect(() => {
    if (!q) { setLoading(false); return; }

    if (skipNextFetch.current) {
      skipNextFetch.current = false;
      setLoading(false);
      return;
    }

    let cancelled = false;
    setData(null); setErr(null); setLoading(true);

    // `from` present ⇒ we're already showing the canonical query.
    const useNofix = nofix || !!from;

    api.search(q, 16, { nofix: useNofix })
      .then((r) => {
        if (cancelled) return;
        const fixed = r.query.normalized.trim();
        const willReplace = !useNofix && !!fixed && fixed !== q.toLowerCase();

        if (willReplace) {
          // Keep the loader on screen until the URL is rewritten — that's
          // when the header search box swaps to the corrected query.
          skipNextFetch.current = true;
          setData(r);
          const sp = new URLSearchParams();
          sp.set("q", fixed);
          sp.set("from", q);
          router.replace(`/search?${sp.toString()}`);
          // loading stays true; the next effect run flips it.
        } else {
          setData(r);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setErr(String(e?.message ?? e));
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

          {/* tiny inline hint — replaces the old banner card */}
          {from && !nofix && (
            <motion.p
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="mt-2 text-xs text-[var(--color-ink-4)]"
            >
              <CornerDownLeft className="inline w-3 h-3 mr-1 -mt-0.5" />
              исправлено из «{from}» ·{" "}
              <Link
                href={`/search?q=${encodeURIComponent(from)}&nofix=1`}
                className="text-[var(--color-accent)] hover:underline"
              >
                искать как написал
              </Link>
            </motion.p>
          )}
          {nofix && (
            <motion.p
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="mt-2 text-xs text-[var(--color-ink-4)]"
            >
              без исправлений ·{" "}
              <Link
                href={`/search?q=${encodeURIComponent(q)}`}
                className="text-[var(--color-accent)] hover:underline"
              >
                включить
              </Link>
            </motion.p>
          )}
        </motion.div>

        {err && (
          <div className="mt-4 p-3 rounded-xl bg-amber-50 text-amber-800 text-sm border border-amber-200">
            {err}
          </div>
        )}

        {loading ? (
          <div className="mt-6"><GridSkeleton count={6} /></div>
        ) : empty ? (
          <EmptyState query={q} />
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

function EmptyState({ query }: { query: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      className="mt-6 card p-10 md:p-14 flex flex-col items-center text-center"
    >
      {/* Stylised emblem — concentric rings + SearchX in the centre.
          Sits on a soft indigo-tinted surface that matches our accent. */}
      <div className="relative grid place-items-center mb-6">
        <span className="absolute w-28 h-28 rounded-full bg-[var(--color-accent-50)]" />
        <span className="absolute w-20 h-20 rounded-full bg-[var(--color-accent-100)]" />
        <span className="relative w-14 h-14 rounded-full bg-white border border-[var(--color-line)] grid place-items-center">
          <SearchX className="w-6 h-6 text-[var(--color-ink-3)]" strokeWidth={1.75} />
        </span>
      </div>

      <h2 className="text-xl md:text-2xl font-semibold tracking-tight">
        Ничего не нашли по{" "}
        <span className="text-[var(--color-accent)]">«{query}»</span>
      </h2>
      <p className="mt-2 text-sm text-[var(--color-ink-4)] max-w-md">
        Источники могли временно ограничить запрос или мы не угадали
        формулировку. Попробуйте короче или с другими словами.
      </p>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-2 text-xs">
        <span className="text-[var(--color-ink-4)]">похожее:</span>
        {["iphone 15", "macbook air m3", "робот пылесос", "sony wh-1000xm5"].map((s) => (
          <Link key={s} href={`/search?q=${encodeURIComponent(s)}`} className="chip">
            {s}
          </Link>
        ))}
      </div>
    </motion.div>
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
