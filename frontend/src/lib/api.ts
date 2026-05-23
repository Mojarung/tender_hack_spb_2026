import type {
  ChatResponse, Favorite, SearchResponse, User,
} from "./types";

const BASE =
  (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_URL : undefined) ??
  "http://127.0.0.1:8000";

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
        region_id: opts?.region_id ?? 213,
      }),
    }),

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
