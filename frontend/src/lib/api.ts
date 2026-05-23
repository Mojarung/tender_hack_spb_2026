import { DEFAULT_REGION_ID } from "./regions";
import type {
  ChatResponse, Favorite, NormalizedQuery, ProductOffer, RankedOffer,
  SearchResponse, Source, User,
} from "./types";

type SearchStreamEventName =
  | "query_normalized" | "source_started" | "offer"
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
  return `${BASE}/api/v1/image-proxy?source=${encodeURIComponent(source)}&url=${encodeURIComponent(url)}`;
}

const STORAGE_KEY = "pp.jwt";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
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
};
