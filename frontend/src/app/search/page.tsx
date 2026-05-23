"use client";

import { motion } from "framer-motion";
import { CornerDownLeft, SearchX } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { ProductCard } from "@/components/ProductCard";
import { GridSkeleton } from "@/components/Skeleton";
import { SmartSuggestionCard } from "@/components/SmartSuggestionCard";
import { api } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { DEFAULT_REGION_ID, getRegion } from "@/lib/regions";
import {
  SOURCE_LABEL, type ProductOffer, type SearchResponse,
  type Source, type SourceGroup, type QueryClarification, type ClarificationOption
} from "@/lib/types";

const EMPTY_GROUP = (source: Source): SourceGroup => ({
  source, count: 0, min_price: null, avg_price: null, median_price: null,
  currency: "RUB", offers: [],
});

function appendOffer(existing: ProductOffer[], offer: ProductOffer): ProductOffer[] {
  // Dedup by url — stream may resend on synonym retry.
  if (existing.some((o) => o.url === offer.url)) return existing;
  return [...existing, offer];
}

export const dynamic = "force-dynamic";

function SearchInner() {
  const router = useRouter();
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const from = (params.get("from") ?? "").trim();    // original user query (after a fix)
  const nofix = params.get("nofix") === "1";
  const regionId = Number(params.get("region_id") ?? DEFAULT_REGION_ID);
  const region = getRegion(regionId);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Source | "all">("all");
  const [clarification, setClarification] = useState<QueryClarification | null>(null);
  // Inline correction display while streaming — populated from query_normalized
  // event so the user sees the canonical query immediately, not after `done`.
  // URL canonicalization still happens on `done` (so the stream isn't aborted).
  const [liveCorrection, setLiveCorrection] = useState<{ from: string; to: string } | null>(null);

  /** When we replace the URL to the corrected query, the effect re-fires
   *  for the new `q`. The next run reuses the data already in state and
   *  just clears the loading flag — no duplicate request. */
  const skipNextFetch = useRef(false);

  const handleClarificationSelect = (option: ClarificationOption) => {
    const isRawSearch = option.query.toLowerCase() === q.toLowerCase() || option.query === q;
    const sp = new URLSearchParams();
    sp.set("q", option.query);
    if (isRawSearch) {
      sp.set("nofix", "1");
    }
    sp.set("region_id", String(region.id));
    router.push(`/search?${sp.toString()}`);
  };

  useEffect(() => {
    if (!q) { setLoading(false); return; }

    if (skipNextFetch.current) {
      skipNextFetch.current = false;
      setLoading(false);
      return;
    }

    let cancelled = false;
    setData({
      query: { raw: q, normalized: q, expansions: [] },
      groups: [], top_deals: [], took_ms: 0, partial: true,
    });
    setErr(null); setLoading(true); setLiveCorrection(null); setClarification(null);

    // `from` present ⇒ we're already showing the canonical query.
    const useNofix = nofix || !!from;

    // Capture the corrected query so we can canonicalize the URL after `done`.
    let normalizedCaptured = "";

    const handle = api.searchStream(q, 16, {
      nofix: useNofix,
      region_id: region.id,
      handlers: {
        onQueryNormalized: (nq) => {
          if (cancelled) return;
          normalizedCaptured = nq.normalized.trim();
          setData((d) => d ? { ...d, query: nq } : d);
          // Show the correction in the page header right away — don't wait
          // for `done`. URL canonicalization stays on `done` so we don't
          // close the in-flight EventSource by triggering a route change.
          if (!useNofix && normalizedCaptured && normalizedCaptured !== q.toLowerCase()) {
            setLiveCorrection({ from: q, to: normalizedCaptured });
          }
        },
        onQueryClarified: (c) => {
          if (cancelled) return;
          setClarification(c);
        },
        onSourceStarted: (e) => {
          if (cancelled) return;
          setData((d) => {
            if (!d) return d;
            if (d.groups.some((g) => g.source === e.source)) return d;
            return { ...d, groups: [...d.groups, EMPTY_GROUP(e.source)] };
          });
        },
        onOffer: (e) => {
          if (cancelled) return;
          setData((d) => {
            if (!d) return d;
            const groups = d.groups.map((g) =>
              g.source === e.source
                ? { ...g, offers: appendOffer(g.offers, e.offer), count: g.offers.length + 1 }
                : g,
            );
            return { ...d, groups };
          });
        },
        onSourceFinished: (e) => {
          if (cancelled) return;
          setData((d) => {
            if (!d) return d;
            const groups = d.groups.map((g) =>
              g.source === e.source
                ? {
                    ...g,
                    count: e.count,
                    min_price: e.min_price,
                    avg_price: e.avg_price,
                    median_price: e.median_price,
                    error: e.error ?? undefined,
                  }
                : g,
            );
            return { ...d, groups };
          });
        },
        onTopDeals: (e) => {
          if (cancelled) return;
          setData((d) => d ? { ...d, top_deals: e.top_deals } : d);
        },
        onDone: (e) => {
          if (cancelled) return;
          const fixed = normalizedCaptured;
          const willReplace = !useNofix && !!fixed && fixed !== q.toLowerCase();
          setData((d) => d ? { ...d, took_ms: e.took_ms, partial: false } : d);
          if (willReplace) {
            skipNextFetch.current = true;
            const sp = new URLSearchParams();
            sp.set("q", fixed);
            sp.set("from", q);
            sp.set("region_id", String(region.id));
            router.replace(`/search?${sp.toString()}`);
            // loading flips on the next effect run, like the old non-stream flow.
          } else {
            setLoading(false);
          }
        },
        onError: (e) => {
          if (cancelled) return;
          setErr(e.message);
          setLoading(false);
        },
      },
    });

    return () => {
      cancelled = true;
      handle.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, nofix, region.id]);

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
  const sourceErrors = data?.groups?.filter((g) => g.error) ?? [];
  const allSourcesFailed = !loading && !!data?.groups?.length && sourceErrors.length === data.groups.length;

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
                label={SOURCE_LABEL[g.source]} count={g.count} error={g.error}
                group={g} currency={g.currency} />
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
          {(() => {
            // Header query — prefer the corrected form once we have it,
            // even mid-stream, so the user doesn't stare at their raw typo.
            const displayedQuery = liveCorrection?.to ?? q;
            return <h1 className="text-2xl font-semibold tracking-tight">Результаты по «{displayedQuery}»</h1>;
          })()}
          <p className="text-sm text-[var(--color-ink-4)] mt-1">
            {loading
              ? `Идёт поиск по региону: ${region.name}…`
              : `Найдено ${all.length} предложений · ${region.name}`}
          </p>
          {region.id !== DEFAULT_REGION_ID && !loading && (
            <p className="text-[11px] text-[var(--color-ink-4)] mt-1 italic">
              регион применяется к Я.Маркету; WB / Ozon / Рунет показывают общий каталог
            </p>
          )}

          {/* Correction notice — `from` is the URL-canonical form (after
              done), `liveCorrection` covers the in-flight stream window. */}
          {(from || liveCorrection) && !nofix && (() => {
            const fromText = from || liveCorrection?.from || "";
            return (
              <motion.p
                initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                className="mt-2 text-xs text-[var(--color-ink-4)]"
              >
                <CornerDownLeft className="inline w-3 h-3 mr-1 -mt-0.5" />
                исправлено из «{fromText}» ·{" "}
                <Link
                  href={`/search?q=${encodeURIComponent(fromText)}&nofix=1&region_id=${region.id}`}
                  className="text-[var(--color-accent)] hover:underline"
                >
                  искать как написал
                </Link>
              </motion.p>
            );
          })()}
          {nofix && (
            <motion.p
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className="mt-2 text-xs text-[var(--color-ink-4)]"
            >
              без исправлений ·{" "}
              <Link
                href={`/search?q=${encodeURIComponent(q)}&region_id=${region.id}`}
                className="text-[var(--color-accent)] hover:underline"
              >
                включить
              </Link>
            </motion.p>
          )}
        </motion.div>

        <SmartSuggestionCard
          clarification={clarification}
          onSelect={handleClarificationSelect}
        />

        {err && (
          <div className="mt-4 p-3 rounded-xl bg-amber-50 text-amber-800 text-sm border border-amber-200">
            {err}
          </div>
        )}

        {allSourcesFailed && (
          <div className="mt-4 p-3 rounded-xl bg-amber-50 text-amber-900 text-sm border border-amber-200">
            Все источники вернули ошибку, поэтому это не похоже на честный пустой результат.
            Проверьте backend-логи или наведите на значки ⚠ в фильтре источников.
          </div>
        )}

        {loading && offers.length === 0 ? (
          <div className="mt-6"><GridSkeleton count={6} /></div>
        ) : empty ? (
          <EmptyState query={q} />
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 mt-6">
              {offers.map((o, i) => (
                <ProductCard key={`${o.source}-${o.url}-${i}`} offer={o} index={i} />
              ))}
            </div>
            {loading && (
              <div className="mt-6 text-xs text-[var(--color-ink-4)] flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-[var(--color-accent)] animate-pulse" />
                ищем в остальных источниках…
              </div>
            )}
          </>
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
  checked, onClick, label, count, error, group, currency,
}: {
  checked: boolean; onClick: () => void; label: string; count: number;
  error?: string | null; group?: SourceGroup; currency?: string;
}) {
  const stats = group && count > 0 ? formatGroupStats(group, currency) : null;
  const subColor = checked ? "text-white/70" : "text-[var(--color-ink-4)]";
  return (
    <li>
      <button
        onClick={onClick}
        title={error ?? undefined}
        className={
          "w-full flex flex-col gap-0.5 px-3 py-2 rounded-lg text-left transition-colors " +
          (checked ? "bg-[var(--color-ink)] text-white"
                   : "hover:bg-[var(--color-surface-2)] text-[var(--color-ink-2)]")
        }
      >
        <span className="flex items-center justify-between gap-3">
          <span>{label}</span>
          <span className={checked ? "text-white/80 text-xs" : "text-xs text-[var(--color-ink-4)]"}>
            {error ? "⚠" : count}
          </span>
        </span>
        {stats && (
          <span className={`text-[11px] tabular-nums ${subColor}`}>{stats}</span>
        )}
      </button>
    </li>
  );
}

function formatGroupStats(g: SourceGroup, currency?: string): string | null {
  const cur = currency ?? g.currency ?? "RUB";
  const parts: string[] = [];
  if (g.min_price) parts.push(`от ${formatPrice(g.min_price, cur)}`);
  if (g.median_price) parts.push(`мед. ${formatPrice(g.median_price, cur)}`);
  else if (g.avg_price) parts.push(`сред. ${formatPrice(g.avg_price, cur)}`);
  return parts.length ? parts.join(" · ") : null;
}
