"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { Bell, BellOff, CornerDownLeft, Download, SearchX } from "lucide-react";
import toast from "react-hot-toast";
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

function offerPriceNum(o: ProductOffer): number {
  const n = Number(o.price);
  return Number.isFinite(n) ? n : Number.NaN;
}

/** Extract the first numeric token from a string like "256 ГБ" → 256. */
function parseFirstNumber(raw: string): number | null {
  const m = raw.match(/-?\d+(?:[.,]\d+)?/);
  if (!m) return null;
  const n = Number(m[0].replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

type SortMode = "relevance" | "price_asc" | "price_desc" | "reviews_desc" | "reviews_asc";

const SORT_LABEL: Record<SortMode, string> = {
  relevance:    "По соответствию",
  price_asc:    "Сначала дешёвые",
  price_desc:   "Сначала дорогие",
  reviews_desc: "Больше отзывов",
  reviews_asc:  "Меньше отзывов",
};

type NumericFacet = {
  kind: "numeric";
  key: string;        // unique id — "__price", "__rating", or char key
  label: string;
  min: number;
  max: number;
  step?: number;      // slider granularity; default 1
  /** Read the offer's value for this facet, or null if it doesn't have one. */
  get: (o: ProductOffer) => number | null;
};

type StringFacet = {
  kind: "string";
  key: string;
  label: string;
  values: Array<[string, number]>;   // value → count, pre-sorted desc by count
  get: (o: ProductOffer) => string | null;
};

type Facet = NumericFacet | StringFacet;

// Characteristic keys to NEVER surface as facets — too noisy, too rare,
// or never useful for narrowing. Case-insensitive contains-match.
const FACET_BLACKLIST = [
  "комплект",       // "Комплектация" — free-form text
  "о магазине", "о продавце",
  "разрывн",        // "Разрывная нагрузка, кгс"
  "декоратив",      // "Декоративные элементы"
  "вес",            // "Вес", "Вес товара"
  "артикул", "штрих", "barcode",
  "гарант",         // "Гарантийный срок"
  "страна",         // "Страна изготовитель" — sparse + low-value
  "ширина", "высота", "длина", "глубина", "толщина",
  "год выпуска", "месяц",
];

// Cosmetic rename for facet labels — backend characteristic keys
// (raw marketplace fields) aren't always user-friendly.
const FACET_LABEL_MAP: Record<string, string> = {
  brand: "Бренд",
  Brand: "Бренд",
  Производитель: "Бренд",
};

function facetLabel(key: string): string {
  return FACET_LABEL_MAP[key] ?? key;
}

function isBlacklistedFacet(key: string): boolean {
  const k = key.toLowerCase();
  return FACET_BLACKLIST.some((bad) => k.includes(bad));
}

/** Facet detector — runs every render over `all`. Cheap for hundreds of offers. */
function buildFacets(all: ProductOffer[]): Facet[] {
  if (all.length === 0) return [];
  const facets: Facet[] = [];

  // Built-in numeric facets first — they're always meaningful.
  const prices = all.map(offerPriceNum).filter(Number.isFinite);
  if (prices.length) {
    facets.push({
      kind: "numeric", key: "__price", label: "Цена",
      min: Math.floor(Math.min(...prices)),
      max: Math.ceil(Math.max(...prices)),
      get: (o) => { const n = offerPriceNum(o); return Number.isFinite(n) ? n : null; },
    });
  }

  const ratings = all.map((o) => o.rating ?? Number(o.characteristics?.rating ?? NaN))
                     .filter((n) => Number.isFinite(n) && n > 0);
  if (ratings.length) {
    facets.push({
      kind: "numeric", key: "__rating", label: "Рейтинг",
      // Rating naturally lives on 0…5 — collapse the min to 0 so the slider
      // doesn't start at "3.7" just because that happens to be the lowest in
      // the current page.
      min: 0, max: 5, step: 0.1,
      get: (o) => {
        const v = o.rating ?? Number(o.characteristics?.rating ?? NaN);
        return Number.isFinite(v) && v > 0 ? v : null;
      },
    });
  }

  const reviewsCounts = all.map((o) => {
    if (o.reviews_count != null) return o.reviews_count;
    const c = o.characteristics?.feedbacks;
    const n = c ? Number(c) : NaN;
    return Number.isFinite(n) ? n : NaN;
  }).filter(Number.isFinite);
  if (reviewsCounts.length) {
    facets.push({
      kind: "numeric", key: "__reviews", label: "Кол-во отзывов",
      min: 0,
      max: Math.ceil(Math.max(...reviewsCounts)),
      get: (o) => {
        if (o.reviews_count != null) return o.reviews_count;
        const v = Number(o.characteristics?.feedbacks ?? NaN);
        return Number.isFinite(v) ? v : null;
      },
    });
  }

  // Dynamic characteristic facets. For each key:
  //   • count how many offers have it
  //   • collect distinct values
  //   • classify: ≥70% values parse as numbers ⇒ numeric slider; else string list
  //
  // Several keys map to the same logical facet (e.g. brand/Brand/Производитель →
  // "Бренд"). We aggregate them under the canonical label so the user sees ONE
  // brand filter instead of three half-empty ones.
  const keyAgg = new Map<string, {
    values: Map<string, number>; nums: number[]; offers: number; rawKeys: Set<string>;
  }>();
  for (const o of all) {
    const chars = o.characteristics ?? {};
    for (const [k, vRaw] of Object.entries(chars)) {
      if (k === "rating" || k === "feedbacks") continue;    // already covered above
      if (isBlacklistedFacet(k)) continue;
      const v = String(vRaw ?? "").trim();
      if (!v) continue;
      const canonical = facetLabel(k);                       // collapse synonyms
      let agg = keyAgg.get(canonical);
      if (!agg) {
        agg = { values: new Map(), nums: [], offers: 0, rawKeys: new Set() };
        keyAgg.set(canonical, agg);
      }
      agg.rawKeys.add(k);
      agg.offers += 1;
      agg.values.set(v, (agg.values.get(v) ?? 0) + 1);
      const n = parseFirstNumber(v);
      if (n !== null) agg.nums.push(n);
    }
  }
  // Coverage threshold — facets present in < 25% of offers add more noise
  // than signal (every other product just gets "passed through" them).
  const minCoverage = Math.max(2, Math.ceil(all.length * 0.25));
  const dynamicFacets: Facet[] = [];
  for (const [label, agg] of keyAgg.entries()) {
    if (agg.values.size < 2) continue;
    if (agg.offers < minCoverage) continue;
    const numericFrac = agg.offers > 0 ? agg.nums.length / agg.offers : 0;
    const isNumeric = numericFrac >= 0.7 && agg.nums.length >= 2 &&
                      Math.max(...agg.nums) > Math.min(...agg.nums);
    const rawKeys = [...agg.rawKeys];
    const readValue = (o: ProductOffer): string | null => {
      const chars = o.characteristics ?? {};
      for (const k of rawKeys) {
        const v = chars[k];
        if (v != null && String(v).trim()) return String(v).trim();
      }
      return null;
    };
    if (isNumeric) {
      dynamicFacets.push({
        kind: "numeric", key: label, label,
        min: Math.floor(Math.min(...agg.nums)),
        max: Math.ceil(Math.max(...agg.nums)),
        get: (o) => { const v = readValue(o); return v ? parseFirstNumber(v) : null; },
      });
    } else {
      const values = [...agg.values.entries()].sort((a, b) => b[1] - a[1]);
      dynamicFacets.push({
        kind: "string", key: label, label, values, get: readValue,
      });
    }
  }
  // Cap the dynamic facets to the most-populated ones — keeps the sidebar
  // scannable. Coverage already filtered out the long tail; this picks the
  // top-N from what survived. 8 fits a 1080p viewport without scrolling.
  dynamicFacets.sort((a, b) => {
    const ca = keyAgg.get(a.label)?.offers ?? 0;
    const cb = keyAgg.get(b.label)?.offers ?? 0;
    return cb - ca;
  });
  facets.push(...dynamicFacets.slice(0, 8));
  return facets;
}

export const dynamic = "force-dynamic";

function SearchInner() {
  const router = useRouter();
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const from = (params.get("from") ?? "").trim();    // original user query (after a fix)
  const nofix = params.get("nofix") === "1";
  // `confirmed=1` is added by handleClarificationSelect so we don't
  // re-prompt the user with the same clarification card after they've
  // picked a variant.
  const confirmed = params.get("confirmed") === "1";
  const regionId = Number(params.get("region_id") ?? DEFAULT_REGION_ID);
  const region = getRegion(regionId);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Filters — all client-side. `numericRanges` and `stringFilters` are
  // keyed by facet.key. `facetSearch` holds the per-facet "filter values"
  // text. Sources that have emitted source_finished are in `finishedSources`.
  const [sourceFilter, setSourceFilter] = useState<Set<Source>>(() => new Set());
  const [numericRanges, setNumericRanges] = useState<Record<string, [number, number]>>({});
  const [stringFilters, setStringFilters] = useState<Record<string, Set<string>>>({});
  const [facetSearch, setFacetSearch] = useState<Record<string, string>>({});
  // Default to relevance — the user wanted "rank by how well the
  // offer matches the query" to be the headline ordering, not price.
  const [sort, setSort] = useState<SortMode>("relevance");
  const [finishedSources, setFinishedSources] = useState<Set<Source>>(() => new Set());
  // Upstream "Did you mean X?" suggestion flow — kept alongside our filters.
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
    // `confirmed=1` skips the clarification preflight on the next
    // render — otherwise we'd ask the same question again. Raw-search
    // also gets `nofix=1` so the spellfixer doesn't rewrite the literal.
    sp.set("confirmed", "1");
    if (isRawSearch) sp.set("nofix", "1");
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
    let handle: { close: () => void } | null = null;

    const startSearch = () => {
      if (cancelled) return;
      setData({
        query: { raw: q, normalized: q, expansions: [] },
        groups: [], top_deals: [], took_ms: 0, partial: true,
      });
      setLoading(true);
      setClarification(null);
      handle = streamSearchInner();
    };

    // ── Pre-flight ambiguity check ──
    // Skip on `confirmed=1` (user already picked an interpretation),
    // on raw-search escape (`nofix=1`) — both signals mean "just search,
    // don't pester me again".
    setErr(null); setLiveCorrection(null); setClarification(null);
    if (confirmed || nofix) {
      startSearch();
    } else {
      setLoading(true);
      api.clarify(q).then((res) => {
        if (cancelled) return;
        if (res.is_ambiguous && res.options.length > 0) {
          setClarification(res);
          setLoading(false);    // wait for user pick
        } else {
          startSearch();
        }
      }).catch(() => {
        // If clarify backend is down, gracefully fall through to search.
        if (!cancelled) startSearch();
      });
    }

    return () => { cancelled = true; handle?.close(); };

    function streamSearchInner(): { close: () => void } {
      setErr(null);
    // New query ⇒ stale filters would silently hide unrelated brands/sources.
    setSourceFilter(new Set());
    setStringFilters({});
    setNumericRanges({});
    setFacetSearch({});
    setFinishedSources(new Set());

    // `from` present ⇒ we're already showing the canonical query.
    const useNofix = nofix || !!from;

    // Capture the corrected query so we can canonicalize the URL after `done`.
    let normalizedCaptured = "";

    return api.searchStream(q, 16, {
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
        // Backend SSE may still emit a clarification mid-stream — we
        // only set it if the user *hasn't* already moved past one
        // (otherwise it'd reappear after their choice).
        onQueryClarified: (c) => {
          if (cancelled || confirmed) return;
          if (c.is_ambiguous && c.options.length > 0) setClarification(c);
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
            // If `source_started` hasn't landed yet (rare with single-queue
            // backend, but possible under EventSource buffering), spin up
            // the group on the fly so the offer isn't silently dropped.
            const exists = d.groups.some((g) => g.source === e.source);
            const base = exists ? d.groups : [...d.groups, EMPTY_GROUP(e.source)];
            const groups = base.map((g) => {
              if (g.source !== e.source) return g;
              const next = appendOffer(g.offers, e.offer);
              return { ...g, offers: next, count: next.length };
            });
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
          setFinishedSources((prev) => {
            const next = new Set(prev);
            next.add(e.source);
            return next;
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
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, nofix, confirmed, region.id]);

  // Track whether we already seeded the sliders for THIS query — fires
  // exactly once per search, on the transition from loading → done.
  const seededForQueryRef = useRef<string | null>(null);
  useEffect(() => {
    if (loading || !data) return;
    if (seededForQueryRef.current === q) return;
    seededForQueryRef.current = q;
    // Use the latest data — recompute facets so we see all offers, not
    // a stale snapshot.
    const allOffers = data.groups.flatMap((g) => g.offers);
    const finalFacets = buildFacets(allOffers);
    setNumericRanges((prev) => {
      const next: Record<string, [number, number]> = { ...prev };
      for (const f of finalFacets) {
        if (f.kind !== "numeric") continue;
        if (!next[f.key]) next[f.key] = [f.min, f.max];
      }
      return next;
    });
  }, [loading, data, q]);

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
  const sourceErrors = data?.groups?.filter((g) => g.error) ?? [];
  const allSourcesFailed = !loading && !!data?.groups?.length && sourceErrors.length === data.groups.length;

  // Per-source median — used by ProductCard's "ДЕМПИНГ −X%" badge.
  // A standalone helper so the empty-array case is just `undefined`.
  const medianByGroup = new Map<Source, number>();
  const sizeByGroup = new Map<Source, number>();
  for (const g of data?.groups ?? []) {
    const ps = g.offers.map((o) => Number(o.price)).filter((n) => n > 0).sort((a, b) => a - b);
    if (ps.length === 0) continue;
    const mid = Math.floor(ps.length / 2);
    medianByGroup.set(g.source, ps.length % 2 ? ps[mid] : (ps[mid - 1] + ps[mid]) / 2);
    sizeByGroup.set(g.source, ps.length);
  }

  // Detect facets from current set. Cheap, runs every render.
  const facets = buildFacets(all);

  // Apply all filters. Missing values are NEVER filter-out — they pass
  // through so a price-only filter doesn't hide products without brand etc.
  const filtered = all.filter((o) => {
    if (sourceFilter.size && !sourceFilter.has(o.source)) return false;
    for (const f of facets) {
      if (f.kind === "numeric") {
        const range = numericRanges[f.key];
        if (!range) continue;
        // Skip if user hasn't moved sliders (range still == facet bounds).
        if (range[0] <= f.min && range[1] >= f.max) continue;
        const v = f.get(o);
        if (v == null) continue;        // missing → keep, per spec
        if (v < range[0] || v > range[1]) return false;
      } else {
        const sel = stringFilters[f.key];
        if (!sel || sel.size === 0) continue;
        const v = f.get(o);
        if (v == null) continue;        // missing → keep
        if (!sel.has(v)) return false;
      }
    }
    return true;
  });

  // Sort filtered.
  const offers = [...filtered].sort((a, b) => {
    switch (sort) {
      case "relevance":
        // Higher relevance first; ties broken by lower price so the
        // top result still feels like "best deal that matches".
        return (Number(b.relevance ?? 0) - Number(a.relevance ?? 0))
            || ((offerPriceNum(a) || 0) - (offerPriceNum(b) || 0));
      case "price_asc":    return (offerPriceNum(a) || 0) - (offerPriceNum(b) || 0);
      case "price_desc":   return (offerPriceNum(b) || 0) - (offerPriceNum(a) || 0);
      case "reviews_desc": return (b.reviews_count ?? 0) - (a.reviews_count ?? 0);
      case "reviews_asc":  return (a.reviews_count ?? 0) - (b.reviews_count ?? 0);
    }
  });

  const activeFilters =
    sourceFilter.size +
    Object.values(stringFilters).reduce((acc, s) => acc + s.size, 0) +
    Object.entries(numericRanges).reduce((acc, [key, [lo, hi]]) => {
      const f = facets.find((x) => x.key === key);
      if (!f || f.kind !== "numeric") return acc;
      return acc + ((lo > f.min || hi < f.max) ? 1 : 0);
    }, 0);

  function resetFilters() {
    setSourceFilter(new Set());
    setStringFilters({});
    // Reset numeric to facet bounds rather than clearing — UX expectation
    // is "all values selected", not "filter section blank".
    setNumericRanges(Object.fromEntries(
      facets.filter((f) => f.kind === "numeric").map((f) =>
        [f.key, [(f as NumericFacet).min, (f as NumericFacet).max]],
      ),
    ));
    setFacetSearch({});
  }

  function toggleSource(s: Source) {
    setSourceFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  }

  function toggleStringValue(key: string, v: string) {
    setStringFilters((prev) => {
      const next = { ...prev };
      const cur = new Set(next[key] ?? new Set<string>());
      if (cur.has(v)) cur.delete(v);
      else cur.add(v);
      next[key] = cur;
      return next;
    });
  }

  function setNumericRange(key: string, range: [number, number]) {
    setNumericRanges((prev) => ({ ...prev, [key]: range }));
  }

  const empty = !loading && all.length === 0;

  return (
    <div className="flex flex-col lg:flex-row gap-6">
      <aside className="lg:w-[260px] shrink-0">
        <div className="card p-5 sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto">
          {/* Лучшие сделки — пинним наверх, до фильтров: жюри видит главное сразу. */}
          {data?.top_deals?.length ? (
            <div className="mb-5 pb-5 border-b border-[var(--color-line)]">
              <div className="text-[11px] uppercase tracking-wider text-[var(--color-ink-4)] mb-2">Лучшие сделки</div>
              <ol className="space-y-1.5">
                {data.top_deals.slice(0, 3).map((d) => (
                  <li key={d.rank} className="flex items-center gap-2 text-sm">
                    <span className="w-6 h-6 rounded-full bg-[var(--color-accent-50)] text-[var(--color-accent-2)] grid place-items-center text-[11px] font-bold">{d.rank}</span>
                    <span className="flex-1 truncate text-[var(--color-ink-2)]">{d.offer.name}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          <div className="flex items-center justify-between mb-3">
            <div className="text-xs uppercase tracking-wider text-[var(--color-ink-4)] font-medium">Фильтры</div>
            {activeFilters > 0 && (
              <button
                type="button"
                onClick={resetFilters}
                className="text-[11px] text-[var(--color-accent)] hover:underline"
              >
                сбросить ({activeFilters})
              </button>
            )}
          </div>

          {/* Source — multi-select with spinner while a source is still searching. */}
          {data?.groups?.length ? (
            <div className="mb-5">
              <div className="text-[11px] uppercase tracking-wider text-[var(--color-ink-4)] mb-2">Источник</div>
              <ul className="space-y-1 text-sm">
                {data.groups.map((g) => (
                  <SourceCheck
                    key={g.source}
                    checked={sourceFilter.has(g.source)}
                    onToggle={() => toggleSource(g.source)}
                    label={SOURCE_LABEL[g.source]}
                    count={g.count}
                    error={g.error}
                    group={g}
                    currency={g.currency}
                    inFlight={loading && !finishedSources.has(g.source)}
                  />
                ))}
              </ul>
            </div>
          ) : null}

          {/* Auto-generated facets — numeric (slider) + string (search+checkbox). */}
          {facets.map((f) =>
            f.kind === "numeric" ? (
              <NumericFilter
                key={f.key}
                facet={f}
                range={numericRanges[f.key] ?? [f.min, f.max]}
                onChange={(r) => setNumericRange(f.key, r)}
              />
            ) : (
              <StringFilter
                key={f.key}
                facet={f}
                selected={stringFilters[f.key] ?? new Set()}
                onToggle={(v) => toggleStringValue(f.key, v)}
                search={facetSearch[f.key] ?? ""}
                onSearchChange={(s) => setFacetSearch((p) => ({ ...p, [f.key]: s }))}
              />
            ),
          )}

          {data?.took_ms != null && (
            <div className="mt-4 pt-4 border-t border-[var(--color-line)] text-xs text-[var(--color-ink-4)]">
              Ответ за <span className="text-[var(--color-ink-2)] font-semibold tabular-nums">{data.took_ms} мс</span>
            </div>
          )}
        </div>
      </aside>

      <div className="flex-1 min-w-0">
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
          <div className="flex items-start justify-between gap-4 flex-wrap">
            {(() => {
              // Header query — prefer the corrected form once we have it,
              // even mid-stream, so the user doesn't stare at their raw typo.
              const displayedQuery = liveCorrection?.to ?? q;
              return <h1 className="text-2xl font-semibold tracking-tight">Результаты по «{displayedQuery}»</h1>;
            })()}
            <div className="flex items-center gap-3 mt-1.5 flex-wrap">
              <NmckMiniButton query={q} regionId={region.id} disabled={loading || all.length < 3} />
              <WatchToggleButton query={q} regionId={region.id} disabled={!q.trim()} />
              <label className="text-xs text-[var(--color-ink-4)] flex items-center gap-2">
                Сортировка:
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value as SortMode)}
                  className="bg-white border border-[var(--color-line)] rounded-full text-sm text-[var(--color-ink-2)] px-3 py-1 focus:outline-none focus:border-[var(--color-accent)]"
                >
                  {(Object.keys(SORT_LABEL) as SortMode[]).map((m) => (
                    <option key={m} value={m}>{SORT_LABEL[m]}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <p className="text-sm text-[var(--color-ink-4)] mt-1">
            {loading
              ? `Идёт поиск по региону: ${region.name}…`
              : activeFilters > 0
                ? `Показано ${offers.length} из ${all.length} · ${region.name}`
                : `Найдено ${all.length} предложений · ${region.name}`}
          </p>
          {region.id !== DEFAULT_REGION_ID && !loading && (
            <p className="text-[11px] text-[var(--color-accent-2)] mt-1 inline-flex items-center gap-1.5 bg-[var(--color-accent-50)] px-2 py-0.5 rounded-full">
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent)]" />
              цены адаптированы под регион «{region.name}»
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
            {/* «Если бы это была закупка» — flagship banner for the
                procurement context. Computes the recommended NMCK on
                the fly (mirrors the server algorithm) and exposes the
                Excel-download CTA inside the card itself. */}
            <AuctionSimulator offers={all} query={liveCorrection?.to ?? q} regionId={region.id} />

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 mt-6">
              {offers.map((o, i) => (
                <ProductCard
                  key={`${o.source}-${o.url}-${i}`}
                  offer={o}
                  index={i}
                  query={liveCorrection?.to ?? q}
                  allOffers={all}
                  groupMedian={medianByGroup.get(o.source)}
                  groupOfferCount={sizeByGroup.get(o.source)}
                />
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
    </motion.div>
  );
}

/** Главная плашка-витрина для жюри: «если бы это была госзакупка —
 *  вот рекомендуемая цена и кнопка скачать обоснование». Считается тем
 *  же алгоритмом, что и Excel-export на бэке (фильтр выбросов + score
 *  по rating × log(reviews)), чтобы цифра на экране совпадала с цифрой
 *  в скачанном файле. */
function AuctionSimulator({
  offers, query, regionId,
}: {
  offers: ProductOffer[];
  query: string;
  regionId: number;
}) {
  const valid = offers.filter((o) => {
    const p = Number(o.price);
    return Number.isFinite(p) && p > 0;
  });
  if (valid.length < 3) return null;

  // Dedup by seller (keep cheapest), filter outliers, score by trust.
  const bySeller = new Map<string, ProductOffer>();
  for (const o of valid) {
    const key = (o.seller || o.source).trim().toLowerCase();
    const cur = bySeller.get(key);
    if (!cur || Number(o.price) < Number(cur.price)) bySeller.set(key, o);
  }
  const pool = [...bySeller.values()];
  const sortedPrices = pool.map((o) => Number(o.price)).sort((a, b) => a - b);
  const m = sortedPrices.length;
  const median = m % 2 ? sortedPrices[(m - 1) / 2]
                       : (sortedPrices[m / 2 - 1] + sortedPrices[m / 2]) / 2;
  // Toss obvious counterfeits (cheap-half outliers) + premium bundles (top outliers).
  let realistic = pool.filter((o) => {
    const p = Number(o.price);
    return p >= median * 0.5 && p <= median * 2.0;
  });
  if (realistic.length < 3) realistic = pool;    // fallback when filter is too aggressive

  const trust = (o: ProductOffer) => {
    const r = Number(o.rating) || 4.0;
    const n = Number(o.reviews_count) || 0;
    return Math.log10(n + 1) * r;
  };
  realistic.sort((a, b) => trust(b) - trust(a)
    || Math.abs(Number(a.price) - median) - Math.abs(Number(b.price) - median));
  const top = realistic.slice(0, 5);
  if (top.length < 3) return null;

  const prices = top.map((o) => Number(o.price));
  const mean = prices.reduce((s, x) => s + x, 0) / prices.length;
  const variance = prices.reduce((s, x) => s + (x - mean) ** 2, 0) / (prices.length - 1);
  const sigma = Math.sqrt(variance);
  const cv = (sigma / mean) * 100;
  const cheapest = Math.min(...prices);
  const homogeneous = cv <= 33.0;
  const fmt = (n: number) => Math.round(n).toLocaleString("ru-RU") + " ₽";

  return (
    <div className="mt-6 rounded-2xl border border-[var(--color-accent-100)] bg-gradient-to-br from-[var(--color-accent-50)] via-white to-white p-6 md:p-7 overflow-hidden relative">
      {/* Декоративный кружок-блик в углу */}
      <div className="absolute -top-20 -right-16 w-64 h-64 rounded-full bg-[var(--color-accent-100)] opacity-25 blur-3xl pointer-events-none" />

      <div className="relative grid md:grid-cols-[1fr_auto] gap-6 items-center">
        <div>
          <div className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-accent-2)] mb-2">
            <span className="grid place-items-center w-5 h-5 rounded-full bg-[var(--color-accent)] text-white text-[10px]">🏛</span>
            Если бы это была государственная закупка
          </div>
          <div className="text-sm text-[var(--color-ink-3)] mb-3 max-w-xl leading-relaxed">
            Это сколько рекомендуется заложить в бюджет на покупку «{query}».
            Считаем по 5 проверенным магазинам — у каждого хороший рейтинг
            и много отзывов.
          </div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-[11px] text-[var(--color-ink-4)] uppercase tracking-wider">Рекомендуемая цена</span>
          </div>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-4xl md:text-5xl font-bold tabular-nums text-[var(--color-accent-2)] tracking-tight">
              {fmt(mean)}
            </span>
            <span className="text-sm text-[var(--color-ink-4)]">за 1 шт.</span>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Pill
              ok
              label={`✓ ${top.length} магазина проверено`}
              hint={`по закону нужно минимум 3`}
            />
            <Pill
              ok={homogeneous}
              label={homogeneous
                ? `✓ Цены сопоставимы (разброс ${cv.toFixed(0)}%)`
                : `⚠ Разные цены (разброс ${cv.toFixed(0)}%)`}
              hint={homogeneous
                ? "норма — отличия меньше трети"
                : "лучше пересмотреть состав магазинов"}
            />
            <Pill
              label={`Самое дешёвое: ${fmt(cheapest)}`}
              hint="ориентир нижней границы торгов"
            />
          </div>
        </div>

        <div className="flex flex-col items-stretch md:items-end gap-2 shrink-0 md:min-w-[200px]">
          <BigDownloadButton query={query} regionId={regionId} />
          <span className="text-[11px] text-[var(--color-ink-4)] text-center md:text-right">
            Excel с расчётом для закупочной документации
          </span>
        </div>
      </div>
    </div>
  );
}


function Pill({ label, hint, ok }: { label: string; hint?: string; ok?: boolean }) {
  const cls = ok === undefined
    ? "bg-white text-[var(--color-ink-2)] border-[var(--color-border)]"
    : ok
      ? "bg-[color-mix(in_srgb,var(--color-good)_14%,white)] text-[var(--color-good)] border-[color-mix(in_srgb,var(--color-good)_30%,transparent)]"
      : "bg-[color-mix(in_srgb,var(--color-warn)_14%,white)] text-[var(--color-warn)] border-[color-mix(in_srgb,var(--color-warn)_30%,transparent)]";
  return (
    <span
      title={hint}
      className={clsx(
        "inline-flex items-center text-xs px-3 py-1.5 rounded-full border font-medium",
        cls,
      )}
    >
      {label}
    </span>
  );
}


/** Small chip-style fallback for the sort row — always present when
 *  the search produced ≥3 offers, so the user can still grab the Excel
 *  even for narrow queries where the big AuctionSimulator banner
 *  doesn't render (it only shows when filtering yields 5 trusted КП). */
function NmckMiniButton({
  query, regionId, disabled,
}: {
  query: string;
  regionId: number;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);
  async function onClick() {
    if (busy) return;
    setBusy(true);
    try {
      await api.nmckExport(query, { region_id: regionId, max_per_source: 10 });
      toast.success("Готово! Excel скачан");
    } catch (e) {
      toast.error(e instanceof Error ? e.message.slice(0, 80) : "не вышло");
    } finally { setBusy(false); }
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      title="Скачать готовое обоснование цены контракта (Excel)"
      className={clsx(
        "inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full transition-colors",
        "bg-[var(--color-accent-50)] text-[var(--color-accent-2)] hover:bg-[var(--color-accent-100)]",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      )}
    >
      <Download className="w-3.5 h-3.5" />
      {busy ? "Готовим…" : "НМЦК · Excel"}
    </button>
  );
}

function BigDownloadButton({ query, regionId }: { query: string; regionId: number }) {
  const [busy, setBusy] = useState(false);
  async function onClick() {
    if (busy) return;
    setBusy(true);
    try {
      await api.nmckExport(query, { region_id: regionId, max_per_source: 10 });
      toast.success("Готово! Excel скачан");
    } catch (e) {
      toast.error(e instanceof Error ? e.message.slice(0, 80) : "не вышло");
    } finally { setBusy(false); }
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={clsx(
        "inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-sm",
        "bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-2)] transition-colors",
        "shadow-[0_8px_24px_rgba(99,102,241,0.25)] hover:shadow-[0_12px_28px_rgba(99,102,241,0.35)]",
        "disabled:opacity-60 disabled:cursor-not-allowed",
      )}
    >
      <Download className="w-4 h-4" />
      {busy ? "Готовим Excel…" : "Скачать обоснование"}
    </button>
  );
}


/** "Следить за ценой" toggle. Looks up the user's watch list on mount;
 *  shows BellOff when there's no matching watch yet, Bell (active) when
 *  one already exists. On click: creates the watch (default 15 min /
 *  ±2 %) or deletes it. After mutation we ping the WatchBell via the
 *  `pp.watch.refresh` custom event so the badge updates immediately. */
function WatchToggleButton({
  query, regionId, disabled,
}: {
  query: string;
  regionId: number;
  disabled: boolean;
}) {
  const [watchId, setWatchId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [initLoading, setInitLoading] = useState(true);
  const queryRef = useRef(query);
  queryRef.current = query;

  // Re-check whether a matching active watch exists. Re-runs on query
  // change AND on the `pp.watch.refresh` event — without the latter,
  // deleting the watch from the WatchBell would leave this button stuck
  // in "Слежу", and the next click would 404 against a removed id.
  useEffect(() => {
    let cancelled = false;
    async function check() {
      setInitLoading(true);
      try {
        const all = await api.watches.list();
        if (cancelled) return;
        const match = all.find(
          (w) => w.active && w.query.trim().toLowerCase() === queryRef.current.trim().toLowerCase(),
        );
        setWatchId(match ? match.id : null);
      } catch {
        if (!cancelled) setWatchId(null);
      } finally {
        if (!cancelled) setInitLoading(false);
      }
    }
    check();
    window.addEventListener("pp.watch.refresh", check);
    return () => {
      cancelled = true;
      window.removeEventListener("pp.watch.refresh", check);
    };
  }, [query]);

  async function onClick() {
    if (loading) return;
    setLoading(true);
    try {
      if (watchId != null) {
        await api.watches.remove(watchId);
        setWatchId(null);
        toast("Слежение снято", { icon: "🔕" });
      } else {
        const w = await api.watches.create({ query, region_id: regionId });
        setWatchId(w.id);
        toast.success(`Слежу за «${query}» — пингну при изменении ≥ ${w.threshold_pct}%`);
      }
      window.dispatchEvent(new Event("pp.watch.refresh"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message.slice(0, 80) : "не вышло");
    } finally { setLoading(false); }
  }

  const active = watchId != null;
  return (
    <button
      type="button"
      disabled={disabled || initLoading || loading}
      onClick={onClick}
      title={active
        ? "Снять с слежения — больше не буду присылать уведомления"
        : "Следить за этим запросом — пингну, когда цена изменится"}
      className={clsx(
        "inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full transition-colors",
        active
          ? "bg-[color-mix(in_srgb,var(--color-good)_18%,transparent)] text-[var(--color-good)] hover:bg-[color-mix(in_srgb,var(--color-good)_28%,transparent)]"
          : "bg-[var(--color-surface-2)] text-[var(--color-ink-3)] hover:bg-[var(--color-accent-50)] hover:text-[var(--color-accent-2)]",
        "disabled:opacity-40 disabled:cursor-not-allowed",
      )}
    >
      {active ? <Bell className="w-3.5 h-3.5" /> : <BellOff className="w-3.5 h-3.5" />}
      {loading ? "…" : active ? "Слежу" : "Следить за ценой"}
    </button>
  );
}


function SourceCheck({
  checked, onToggle, label, count, error, group, currency, inFlight,
}: {
  checked: boolean;
  onToggle: () => void;
  label: string;
  count: number;
  error?: string | null;
  group?: SourceGroup;
  currency?: string;
  inFlight?: boolean;
}) {
  const stats = group && count > 0 ? formatGroupStats(group, currency) : null;
  return (
    <li>
      <label
        title={error ?? undefined}
        className="flex items-start gap-2 px-1 py-1.5 rounded-lg cursor-pointer hover:bg-[var(--color-surface-2)]"
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-0.5 accent-[var(--color-accent)]"
          aria-label={`Источник: ${label}`}
        />
        <span className="flex-1 min-w-0">
          <span className="flex items-center justify-between gap-3">
            <span className="text-[var(--color-ink-2)] flex items-center gap-1.5">
              {label}
              {inFlight && (
                <span className="dot-pulse" aria-label="ещё ищем" title="ещё ищем" />
              )}
            </span>
            <span className="text-xs text-[var(--color-ink-4)] tabular-nums">
              {error ? "⚠" : inFlight && count === 0 ? "…" : count}
            </span>
          </span>
          {stats && (
            <span className="block text-[11px] tabular-nums text-[var(--color-ink-4)]">{stats}</span>
          )}
        </span>
      </label>
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

/** Numeric facet — dual-range slider + min/max number inputs. */
function NumericFilter({
  facet, range, onChange,
}: {
  facet: NumericFacet;
  range: [number, number];
  onChange: (r: [number, number]) => void;
}) {
  // The slider can't represent < 2 distinct values — render the facet
  // as a no-op label instead of a broken input.
  if (facet.max <= facet.min) return null;

  // For prices in the thousands, percent-based label is more readable
  // than the raw number; recharts-style abbreviation kept out of scope.
  const isPriceLike = facet.key === "__price" || facet.label === "Цена";
  const step = facet.step ?? 1;
  // Show 1 decimal for fractional steps (rating), integers otherwise.
  const fmt = (n: number) =>
    isPriceLike
      ? formatPrice(String(Math.round(n)), "RUB")
      : step < 1
        ? n.toFixed(1)
        : String(Math.round(n));

  function setLo(v: number) {
    const lo = Math.min(Math.max(v, facet.min), range[1]);
    onChange([lo, range[1]]);
  }
  function setHi(v: number) {
    const hi = Math.max(Math.min(v, facet.max), range[0]);
    onChange([range[0], hi]);
  }

  return (
    <div className="mb-5">
      <div className="text-[11px] uppercase tracking-wider text-[var(--color-ink-4)] mb-2">
        {facet.label}
      </div>
      <DualRangeSlider
        min={facet.min}
        max={facet.max}
        step={step}
        value={range}
        onChange={onChange}
      />
      <div className="flex items-center gap-2 mt-2">
        <input
          type="number"
          inputMode={step < 1 ? "decimal" : "numeric"}
          step={step}
          value={fmt(range[0])}
          onChange={(e) => setLo(Number(e.target.value))}
          className="input !py-1 !px-2 text-xs w-full tabular-nums"
          aria-label={`${facet.label} от`}
        />
        <span className="text-[11px] text-[var(--color-ink-4)]">—</span>
        <input
          type="number"
          inputMode={step < 1 ? "decimal" : "numeric"}
          step={step}
          value={fmt(range[1])}
          onChange={(e) => setHi(Number(e.target.value))}
          className="input !py-1 !px-2 text-xs w-full tabular-nums"
          aria-label={`${facet.label} до`}
        />
      </div>
      <div className="text-[10px] text-[var(--color-ink-4)] mt-1 flex justify-between tabular-nums">
        <span>{fmt(facet.min)}</span>
        <span>{fmt(facet.max)}</span>
      </div>
    </div>
  );
}

/** String facet — own search input + checkbox list with counts. */
function StringFilter({
  facet, selected, onToggle, search, onSearchChange,
}: {
  facet: StringFacet;
  selected: Set<string>;
  onToggle: (v: string) => void;
  search: string;
  onSearchChange: (s: string) => void;
}) {
  const q = search.trim().toLowerCase();
  const filtered = q
    ? facet.values.filter(([v]) => v.toLowerCase().includes(q))
    : facet.values;
  return (
    <div className="mb-5">
      <div className="text-[11px] uppercase tracking-wider text-[var(--color-ink-4)] mb-2 flex items-center justify-between">
        <span className="truncate" title={facet.label}>{facet.label}</span>
        {selected.size > 0 && (
          <span className="normal-case text-[10px] text-[var(--color-accent)] font-normal">
            +{selected.size}
          </span>
        )}
      </div>
      {facet.values.length > 5 && (
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={`найти в "${facet.label.toLowerCase()}"`}
          className="input !py-1 !px-2 text-xs w-full mb-1.5"
        />
      )}
      <ul className="space-y-0.5 text-sm max-h-[180px] overflow-y-auto pr-1">
        {filtered.length === 0 && (
          <li className="text-[11px] text-[var(--color-ink-4)] py-1">ничего не найдено</li>
        )}
        {filtered.map(([value, count]) => (
          <li key={value}>
            <label className="flex items-center gap-2 py-1 cursor-pointer hover:text-[var(--color-ink)]">
              <input
                type="checkbox"
                checked={selected.has(value)}
                onChange={() => onToggle(value)}
                className="accent-[var(--color-accent)]"
              />
              <span className="flex-1 truncate text-[var(--color-ink-2)]" title={value}>{value}</span>
              <span className="text-[11px] text-[var(--color-ink-4)] tabular-nums">{count}</span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Two-thumb range slider. Two overlapping native inputs over a tracked
 *  rail — keyboard-accessible, no library dependency. */
function DualRangeSlider({
  min, max, step = 1, value, onChange,
}: {
  min: number;
  max: number;
  step?: number;
  value: [number, number];
  onChange: (r: [number, number]) => void;
}) {
  const [lo, hi] = value;
  const span = Math.max(max - min, step);
  const pctLo = ((lo - min) / span) * 100;
  const pctHi = ((hi - min) / span) * 100;
  // Both inputs sit on the same horizontal pixel range. The lower thumb
  // floats to the top half of the rail when it's near the right edge so it
  // stays grabbable.
  return (
    <div className="range-dual">
      <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-[3px] rounded-full bg-[var(--color-surface-2)]" />
      <div
        className="absolute top-1/2 -translate-y-1/2 h-[3px] rounded-full bg-[var(--color-accent)]"
        style={{ left: `${pctLo}%`, right: `${100 - pctHi}%` }}
      />
      <input
        type="range" min={min} max={max} step={step} value={lo}
        onChange={(e) => {
          const v = Math.min(Number(e.target.value), hi);
          onChange([v, hi]);
        }}
        aria-label="нижняя граница"
        style={{ zIndex: pctLo > 90 ? 3 : 2 }}
      />
      <input
        type="range" min={min} max={max} step={step} value={hi}
        onChange={(e) => {
          const v = Math.max(Number(e.target.value), lo);
          onChange([lo, v]);
        }}
        aria-label="верхняя граница"
        style={{ zIndex: 3 }}
      />
    </div>
  );
}
