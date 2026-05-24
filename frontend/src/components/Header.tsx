"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { Clock, Heart, LogOut, MapPin, Search, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-hook";
import { history } from "@/lib/history";
import { DEFAULT_REGION_ID, RUSSIA_REGIONS, getRegion } from "@/lib/regions";
import { useHistory } from "@/lib/use-history";
import { Logo } from "./Logo";
import { WatchBell } from "./WatchBell";

const REGION_STORAGE_KEY = "pp.region_id";

function getStoredRegionId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(REGION_STORAGE_KEY);
}

function setStoredRegionId(regionId: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(REGION_STORAGE_KEY, regionId);
  window.dispatchEvent(new Event("pp.region"));
}

function SearchBox() {
  const router = useRouter();
  const params = useSearchParams();
  const items = useHistory();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [regionId, setRegionId] = useState(params.get("region_id") ?? String(DEFAULT_REGION_ID));
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { setQ(params.get("q") ?? ""); }, [params]);

  useEffect(() => {
    const next = params.get("region_id") ?? getStoredRegionId() ?? String(DEFAULT_REGION_ID);
    setRegionId(next);
  }, [params]);

  useEffect(() => {
    function onRegionChange() {
      setRegionId(getStoredRegionId() ?? String(DEFAULT_REGION_ID));
    }
    window.addEventListener("pp.region", onRegionChange);
    return () => window.removeEventListener("pp.region", onRegionChange);
  }, []);

  // Close dropdown when clicking outside.
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function submit(value: string) {
    const t = value.trim();
    if (!t) return;
    history.push(t);
    setOpen(false);
    router.push(`/search?q=${encodeURIComponent(t)}&region_id=${regionId}`);
  }

  return (
    <div ref={wrapRef} className="flex-1 max-w-[640px] relative">
      <form onSubmit={(e) => { e.preventDefault(); submit(q); }}>
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-ink-4)]" />
        <input
          value={q}
          onFocus={() => setOpen(true)}
          onChange={(e) => setQ(e.target.value)}
          placeholder="iphone 15, macbook, кофемашина..."
          className="input pl-11 pr-4 py-2.5 text-sm rounded-full"
        />
      </form>

      <AnimatePresence>
        {open && items.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="absolute top-full mt-2 left-0 right-0 card !rounded-2xl p-2 z-50 shadow-[0_24px_48px_rgba(11,13,18,0.10)]"
          >
            <div className="flex items-center justify-between px-2 py-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--color-ink-4)]">
                Недавние поиски
              </span>
              <button
                type="button"
                onClick={() => history.clear()}
                className="text-xs text-[var(--color-ink-4)] hover:text-[var(--color-ink-2)] inline-flex items-center gap-1"
              >
                <Trash2 className="w-3 h-3" /> Очистить
              </button>
            </div>
            <ul>
              {items.slice(0, 8).map((it) => (
                /* Two SIBLING buttons inside a relative <li>.
                 * Nesting <button> inside <button> is invalid HTML and
                 * triggers a hydration error in React 19. The "X"
                 * remove button is absolutely positioned over the
                 * row's right edge. */
                <li key={it.q} className="group relative">
                  <button
                    type="button"
                    onClick={() => submit(it.q)}
                    className="w-full flex items-center gap-3 px-2 py-2 pr-9 rounded-lg hover:bg-[var(--color-surface-2)] text-left"
                  >
                    <Clock className="w-4 h-4 text-[var(--color-ink-4)]" />
                    <span className="flex-1 truncate text-sm text-[var(--color-ink-2)]">{it.q}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => history.remove(it.q)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 -m-1 rounded hover:bg-white/60"
                    aria-label="Убрать из истории"
                  >
                    <X className="w-3.5 h-3.5 text-[var(--color-ink-4)]" />
                  </button>
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function RegionSelect() {
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const [regionId, setRegionId] = useState(params.get("region_id") ?? String(DEFAULT_REGION_ID));
  const current = getRegion(Number(regionId));

  useEffect(() => {
    const next = params.get("region_id") ?? getStoredRegionId() ?? String(DEFAULT_REGION_ID);
    setRegionId(next);
  }, [params]);

  function changeRegion(regionId: string) {
    setRegionId(regionId);
    setStoredRegionId(regionId);
    const next = new URLSearchParams(params.toString());
    next.set("region_id", regionId);
    if (q.trim()) router.push(`/search?${next.toString()}`);
  }

  return (
    <label
      className="relative hidden lg:block"
      title="Регион поиска. Применяется к Я.Маркету; WB / Ozon / Рунет используют общий каталог."
    >
      <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-ink-4)] pointer-events-none" />
      <select
        value={current.id}
        onChange={(e) => changeRegion(e.target.value)}
        className="appearance-none w-[210px] pl-9 pr-8 py-2.5 rounded-full bg-white border border-[var(--color-line)] text-sm text-[var(--color-ink-2)] shadow-[0_8px_24px_rgba(11,13,18,0.04)] focus:outline-none focus:border-[var(--color-accent)]"
        aria-label="Регион поиска"
      >
        {RUSSIA_REGIONS.map((region) => (
          <option key={`${region.id}-${region.name}`} value={region.id}>
            {region.name}
          </option>
        ))}
      </select>
      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-[var(--color-ink-4)] pointer-events-none">
        ▾
      </span>
    </label>
  );
}

function Avatar({ name }: { name: string }) {
  // First grapheme of the display name (or e-mail before "@") in a coloured ring.
  const ch = (name || "?").trim().charAt(0).toUpperCase();
  return (
    <div className="relative">
      <div
        className={clsx(
          "w-9 h-9 rounded-full grid place-items-center text-white font-semibold text-sm",
          "bg-gradient-to-br from-indigo-500 to-fuchsia-500",
        )}
      >
        {ch}
      </div>
      <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-400 border-2 border-white" />
    </div>
  );
}

export function Header() {
  const router = useRouter();
  const { user, loading } = useAuth();

  return (
    <motion.header
      initial={{ y: -16, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="sticky top-0 z-30 bg-[color:var(--color-bg)]/85 backdrop-blur border-b border-[var(--color-line)]"
    >
      <div className="max-w-[1240px] mx-auto px-6 h-16 flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2 group" aria-label="PricePulse — главная">
          <Logo size={32} />
          <span className="font-semibold tracking-tight">PricePulse</span>
        </Link>

        <Suspense fallback={<div className="flex-1" />}>
          <SearchBox />
        </Suspense>

        <Suspense fallback={null}>
          <RegionSelect />
        </Suspense>

        <nav className="flex items-center gap-2">
          <WatchBell />
          <Link href="/favorites" className="btn btn-ghost !py-2 !px-3 rounded-full" aria-label="Избранное">
            <Heart className="w-4 h-4" />
          </Link>

          {loading ? (
            <div className="w-9 h-9 rounded-full bg-[var(--color-surface-2)] animate-pulse" />
          ) : user ? (
            <div className="flex items-center gap-2.5 pl-1">
              <Avatar name={user.display_name ?? user.email ?? "?"} />
              <span className="hidden md:block text-sm font-medium text-[var(--color-ink-2)] max-w-[140px] truncate">
                {user.display_name ?? user.email.split("@")[0]}
              </span>
              <button
                onClick={() => { api.auth.logout(); router.push("/"); }}
                className="btn btn-ghost !p-2 rounded-full"
                aria-label="Выйти"
                title="Выйти"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <>
              <Link href="/login" className="btn btn-ghost !py-2 rounded-full hidden sm:inline-flex">
                Войти
              </Link>
              <Link href="/register" className="btn btn-primary !py-2 rounded-full">
                Создать аккаунт
              </Link>
            </>
          )}
        </nav>
      </div>
    </motion.header>
  );
}
