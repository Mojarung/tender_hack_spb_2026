import { DEFAULT_REGION_ID } from "./regions";
import type {
  ChatResponse, Favorite, NormalizedQuery, PriceAlert, PriceWatch, ProductOffer,
  QueryClarification, RankedOffer, SearchResponse, Source, User,
} from "./types";

type SearchStreamEventName =
  | "query_normalized" | "query_clarified" | "source_started" | "offer"
  | "source_finished" | "top_deals" | "done";

export interface SourceFinishedEvent {
  source: Source;
  count: number;
  min_price: string | null;
  avg_price: string | null;
  median_price: string | null;
  error: string | null;
  cached: boolean;
}

export interface SearchStreamHandlers {
  onQueryNormalized?: (q: NormalizedQuery) => void;
  onQueryClarified?: (c: QueryClarification) => void;
  onSourceStarted?: (e: { source: Source }) => void;
  onOffer?: (e: { source: Source; offer: ProductOffer }) => void;
  onSourceFinished?: (e: SourceFinishedEvent) => void;
  onTopDeals?: (e: { top_deals: RankedOffer[] }) => void;
  onDone?: (e: { took_ms: number }) => void;
  onError?: (err: Error) => void;
}

export interface SearchStreamOptions {
  nofix?: boolean;
  region_id?: number;
  handlers?: SearchStreamHandlers;
}

export interface SearchStreamHandle { close: () => void }

const BASE =
  (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_URL : undefined) ??
  "http://127.0.0.1:8000";

/** Wrap a marketplace image URL in our caching proxy. Backend 302s either
 *  to the cached MinIO copy (first hit warms it) or to the original URL
 *  if MinIO is down — so this is always safe to call. */
export function proxyImage(url: string | null | undefined, source: string): string {
  if (!url) return "";
  // Inline data: URIs (Google Shopping ships base64 placeholders) — pass
  // them straight to <img> instead of round-tripping through the proxy.
  // Backend image-proxy rejects URLs > 2 kB anyway.
  if (url.startsWith("data:")) return url;
  return `${BASE}/api/v1/image-proxy?source=${encodeURIComponent(source)}&url=${encodeURIComponent(url)}`;
}

const STORAGE_KEY = "pp.jwt";
const ANON_KEY = "pp.anon_id";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
}

/** Stable per-browser identifier the watches endpoints use when there's
 *  no JWT. Generated on first read, kept forever (cleared only when the
 *  user manually wipes localStorage). */
export function getAnonId(): string {
  if (typeof window === "undefined") return "ssr-placeholder";
  let v = window.localStorage.getItem(ANON_KEY);
  if (!v) {
    v = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Array.from(crypto.getRandomValues(new Uint8Array(16)))
          .map((b) => b.toString(16).padStart(2, "0")).join("");
    window.localStorage.setItem(ANON_KEY, v);
  }
  return v;
}

function setToken(t: string | null) {
  if (typeof window === "undefined") return;
  if (t) window.localStorage.setItem(STORAGE_KEY, t);
  else window.localStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event("pp.auth"));
}

async function http<T>(path: string, init: RequestInit = {}, expect = "json"): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.body ? { "Content-Type": "application/json" } : {}),
  };
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  // Always identify the browser so the watches endpoints work without
  // a login. Backend ignores it when a valid JWT is present.
  if (typeof window !== "undefined") headers["X-Anon-Id"] = getAnonId();
  Object.assign(headers, init.headers ?? {});

  const res = await fetch(BASE + path, { ...init, headers });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string"
        ? body.detail
        : Array.isArray(body?.detail)
          ? body.detail.map((d: { msg?: string }) => d?.msg ?? "").join("; ")
          : JSON.stringify(body).slice(0, 200);
    } catch { /* not json */ }
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  if (expect === "text") return (await res.text()) as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => http<{ status: string }>("/health"),

  search: (
    query: string,
    max_per_source = 6,
    opts?: { nofix?: boolean; region_id?: number },
  ) =>
    http<SearchResponse>("/api/v1/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        max_per_source,
        nofix: opts?.nofix ?? false,
        region_id: opts?.region_id ?? DEFAULT_REGION_ID,
      }),
    }),

  /** Cheap pre-flight: ask Gemma if the query is ambiguous (e.g.
   *  "лодка и яблоко" mixes two unrelated products). The frontend
   *  uses this BEFORE kicking off a full search so we don't waste a
   *  multi-source scrape on a doomed literal query. */
  clarify: (query: string) =>
    http<QueryClarification>("/api/v1/search/clarify", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  /** Image → text-query via Gemma 4 vision. User uploads a product
   *  photo, backend asks the model to describe it as a 3-7 word search
   *  query in Russian, returns the string for the normal /search flow. */
  searchByImage: async (file: File): Promise<{ query: string; used_model: string }> => {
    const form = new FormData();
    form.append("image", file);
    const t = getToken();
    const headers: Record<string, string> = {};
    if (t) headers.Authorization = `Bearer ${t}`;
    if (typeof window !== "undefined") headers["X-Anon-Id"] = getAnonId();
    const res = await fetch(`${BASE}/api/v1/search/image`, {
      method: "POST", body: form, headers,
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(txt || `${res.status} ${res.statusText}`);
    }
    return await res.json();
  },

  searchStream: (
    query: string,
    max_per_source = 16,
    opts: SearchStreamOptions = {},
  ): SearchStreamHandle => {
    const params = new URLSearchParams({
      query,
      max_per_source: String(max_per_source),
      region_id: String(opts.region_id ?? DEFAULT_REGION_ID),
      nofix: String(opts.nofix ?? false),
    });
    const url = `${BASE}/api/v1/search/stream?${params.toString()}`;
    const es = new EventSource(url);
    const handlers = opts.handlers ?? {};

    const wire = <T,>(name: SearchStreamEventName, fn?: (data: T) => void) => {
      if (!fn) return;
      es.addEventListener(name, (evt: MessageEvent) => {
        try { fn(JSON.parse(evt.data) as T); } catch { /* ignore malformed */ }
      });
    };

    wire<NormalizedQuery>("query_normalized", handlers.onQueryNormalized);
    wire<QueryClarification>("query_clarified", handlers.onQueryClarified);
    wire<{ source: Source }>("source_started", handlers.onSourceStarted);
    wire<{ source: Source; offer: ProductOffer }>("offer", handlers.onOffer);
    wire<SourceFinishedEvent>("source_finished", handlers.onSourceFinished);
    wire<{ top_deals: RankedOffer[] }>("top_deals", handlers.onTopDeals);

    // 'done' is the only event we always own — close the connection so the
    // browser doesn't auto-reconnect after the search is complete.
    es.addEventListener("done", (evt: MessageEvent) => {
      try {
        const data = JSON.parse(evt.data) as { took_ms: number };
        handlers.onDone?.(data);
      } catch { /* ignore malformed */ }
      es.close();
    });

    // Browser only fires onerror after the first reconnect attempt; we
    // surface it so the UI can stop the spinner.
    es.onerror = () => {
      // readyState === CLOSED → terminal failure (not a transient blip).
      if (es.readyState === EventSource.CLOSED) {
        handlers.onError?.(new Error("stream closed"));
      }
    };

    return { close: () => es.close() };
  },

  watches: {
    list: () => http<PriceWatch[]>("/api/v1/watches"),
    create: (b: { query: string; interval_min?: number; threshold_pct?: number; region_id?: number }) =>
      http<PriceWatch>("/api/v1/watches", {
        method: "POST",
        body: JSON.stringify({
          query: b.query,
          interval_min: b.interval_min ?? 15,
          threshold_pct: b.threshold_pct ?? 2.0,
          region_id: b.region_id ?? DEFAULT_REGION_ID,
        }),
      }),
    remove: (id: number) =>
      http<void>(`/api/v1/watches/${id}`, { method: "DELETE" }),
    alerts: (opts: { unread?: boolean; limit?: number } = {}) => {
      const p = new URLSearchParams();
      if (opts.unread) p.set("unread", "true");
      if (opts.limit) p.set("limit", String(opts.limit));
      const qs = p.toString();
      return http<PriceAlert[]>(`/api/v1/watches/alerts${qs ? `?${qs}` : ""}`);
    },
    unreadCount: () =>
      http<{ unread: number }>("/api/v1/watches/alerts/count"),
    markRead: (id: number) =>
      http<void>(`/api/v1/watches/alerts/${id}/read`, { method: "POST" }),
    markAllRead: () =>
      http<{ unread: number }>("/api/v1/watches/alerts/read-all", { method: "POST" }),
  },

  favorites: {
    list: () => http<Favorite[]>("/api/v1/favorites"),
    add: (b: Omit<Favorite, "id" | "added_at">) =>
      http<Favorite>("/api/v1/favorites", { method: "POST", body: JSON.stringify(b) }),
    remove: (id: number) =>
      http<void>(`/api/v1/favorites/${id}`, { method: "DELETE" }),
  },

  auth: {
    me: () => http<User>("/users/me"),

    register: (email: string, password: string, display_name?: string) =>
      http<User>("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name }),
      }),

    login: async (email: string, password: string) => {
      const form = new URLSearchParams({ username: email, password });
      const res = await fetch(`${BASE}/auth/jwt/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: form,
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body ? body.slice(0, 200) : `${res.status} ${res.statusText}`);
      }
      const { access_token } = (await res.json()) as { access_token: string };
      setToken(access_token);
      return access_token;
    },

    logout: () => setToken(null),
  },

  chat: (message: string, session_id?: string) =>
    http<ChatResponse>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id }),
    }),

  /** Lazily resolve a Google Shopping card to its real merchant URL.
   *  Backend opens Google in a stealth browser, trusted-clicks the card,
   *  captures the redirect — ~5-10 s the first time, instant on cache hit. */
  runetResolve: (query: string, title: string, seller?: string | null) =>
    http<{ url: string | null }>("/api/v1/runet/resolve", {
      method: "POST",
      body: JSON.stringify({ query, title, seller: seller ?? null }),
    }),

  /** Price history points for a given offer (latest first). Empty list
   *  when nothing is captured yet — the sparkline degrades to a single
   *  dot in that case. */
  priceHistory: (source: string, itemId: string, limit = 60) =>
    http<{ source: string; item_id: string; count: number;
           points: { ts: string; price: string }[] }>(
      `/api/v1/price-history/${source}/${encodeURIComponent(itemId)}?limit=${limit}`,
    ),

  /** Stream an AI-generated "why this is a good deal" explanation. Returns
   *  a callable that yields fragments; close() aborts the request. Falls
   *  back to a static summary if Ollama is down. */
  explainStream: (
    query: string,
    offer: { source: string; name: string; price: string; seller?: string | null;
             rating?: number | null; reviews_count?: number | null; url?: string | null },
    allOffers: { source: string; name: string; price: string; seller?: string | null;
                 rating?: number | null; reviews_count?: number | null }[],
    onChunk: (text: string) => void,
    onDone?: () => void,
    onError?: (msg: string) => void,
  ): { close: () => void } => {
    const ctrl = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${BASE}/api/v1/explain`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: ctrl.signal,
          body: JSON.stringify({ query, offer, all_offers: allOffers }),
        });
        if (!res.ok || !res.body) {
          onError?.(`${res.status} ${res.statusText}`);
          return;
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder("utf-8");
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          // SSE frames: split on double-newline
          const frames = buf.split("\n\n");
          buf = frames.pop() ?? "";
          for (const frame of frames) {
            if (!frame.startsWith("data: ")) continue;
            const payload = frame.slice(6);
            if (payload === "[DONE]") { onDone?.(); return; }
            if (payload.startsWith("[ERROR]")) { onError?.(payload.slice(7).trim()); return; }
            onChunk(payload.replace(/\\n/g, "\n"));
          }
        }
        onDone?.();
      } catch (e) {
        if (ctrl.signal.aborted) return;
        onError?.(e instanceof Error ? e.message : String(e));
      }
    })();
    return { close: () => ctrl.abort() };
  },

  /** LLM aspect extraction from product reviews — returns pros/cons chips
   *  + an overall sentiment score (0..100). Cached server-side for 24 h
   *  per (offer_url, review_count). Use only when there are ≥3 reviews
   *  with real text — otherwise the model invents aspects. */
  aspects: (offer_url: string, reviews: string[]) =>
    http<{
      pros: { label: string; mentions: number }[];
      cons: { label: string; mentions: number }[];
      score: number;
      n_reviews_used: number;
    }>("/api/v1/aspects", {
      method: "POST",
      body: JSON.stringify({ offer_url, reviews }),
    }),

  /** Run a search and download the result as a 44-ФЗ Приложение №1 Excel.
   *  Backend re-fetches via the orchestrator (uses cache), assembles the
   *  workbook, returns it as a blob. We trigger the browser download here. */
  nmckExport: async (
    query: string,
    opts: { max_per_source?: number; region_id?: number; quantity?: number } = {},
  ) => {
    const res = await fetch(`${BASE}/api/v1/nmck/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        max_per_source: opts.max_per_source ?? 10,
        region_id: opts.region_id ?? DEFAULT_REGION_ID,
        quantity: opts.quantity ?? 1,
      }),
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(txt || `${res.status} ${res.statusText}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `НМЦК_${query.slice(0, 60).replace(/\s+/g, "_")}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  },
};
