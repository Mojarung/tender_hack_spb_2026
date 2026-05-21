/* Thin browser-side API client. All requests go through Next.js rewrites
   (`/api/backend/...` → backend), so we stay same-origin and cookies work. */

import type {
  ChatResponse,
  Favorite,
  PriceHistoryResponse,
  SearchResponse,
  SentimentResponse,
  Source,
  User,
} from "./types";

// `NEXT_PUBLIC_API_URL` is baked at build time. Default to a host-side
// dev backend so `pnpm dev` works out of the box; in prod set it to your
// deployed backend.
const BASE =
  (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_URL : undefined) ??
  "http://127.0.0.1:8000";

function authHeader(): HeadersInit {
  if (typeof window === "undefined") return {};
  const t = window.localStorage.getItem("pp.jwt");
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function http<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  search: (query: string, max_per_source = 5) =>
    http<SearchResponse>("/api/v1/search", {
      method: "POST",
      body: JSON.stringify({ query, max_per_source }),
    }),

  priceHistory: (source: Source, item_id: string, limit = 100) =>
    http<PriceHistoryResponse>(
      `/api/v1/price-history/${source}/${encodeURIComponent(item_id)}?limit=${limit}`,
    ),

  sentiment: (source: Source, item_id: string | number, sample = 100) =>
    http<SentimentResponse>(
      `/api/v1/sentiment/${source}/${item_id}?sample=${sample}`,
    ),

  favorites: {
    list: () => http<Favorite[]>("/api/v1/favorites"),
    add: (body: Omit<Favorite, "id" | "added_at">) =>
      http<Favorite>("/api/v1/favorites", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    remove: (id: number) =>
      http<void>(`/api/v1/favorites/${id}`, { method: "DELETE" }),
  },

  auth: {
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
      if (!res.ok) throw new Error(`login failed: ${res.status}`);
      const { access_token } = (await res.json()) as { access_token: string };
      window.localStorage.setItem("pp.jwt", access_token);
      return access_token;
    },
    logout: () => {
      window.localStorage.removeItem("pp.jwt");
    },
    me: () => http<User>("/users/me"),
  },

  chat: (message: string, session_id?: string) =>
    http<ChatResponse>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id }),
    }),
};
