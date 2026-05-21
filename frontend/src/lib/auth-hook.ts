"use client";

import { useEffect, useState } from "react";

import { api, getToken } from "./api";
import type { User } from "./types";

/** Tiny reactive auth state — listens to the `pp.auth` event we emit
 *  from `setToken`. No context provider needed: lightweight and fast. */
export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function fetchMe() {
      const t = getToken();
      if (!t) { if (!cancelled) { setUser(null); setLoading(false); } return; }
      try {
        const u = await api.auth.me();
        if (!cancelled) setUser(u);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    fetchMe();
    const handler = () => { setLoading(true); fetchMe(); };
    window.addEventListener("pp.auth", handler);
    return () => { cancelled = true; window.removeEventListener("pp.auth", handler); };
  }, []);

  return { user, loading };
}
