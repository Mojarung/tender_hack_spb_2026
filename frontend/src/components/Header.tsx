"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Heart, LogOut, Search, User as UserIcon } from "lucide-react";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-hook";

function SearchBox() {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");

  useEffect(() => { setQ(params.get("q") ?? ""); }, [params]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!q.trim()) return;
        router.push(`/search?q=${encodeURIComponent(q.trim())}`);
      }}
      className="flex-1 max-w-[640px] relative"
    >
      <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-ink-4)]" />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="iphone 15, macbook, кофемашина..."
        className="input pl-11 pr-4 py-2.5 text-sm rounded-full"
      />
    </form>
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
        <Link href="/" className="flex items-center gap-2 group">
          <span className="w-8 h-8 rounded-xl bg-[var(--color-ink)] grid place-items-center text-white font-bold text-sm transition-transform group-hover:scale-105">
            P
          </span>
          <span className="font-semibold tracking-tight">PricePulse</span>
        </Link>

        <Suspense fallback={<div className="flex-1" />}>
          <SearchBox />
        </Suspense>

        <nav className="flex items-center gap-2">
          <Link href="/favorites" className="btn btn-ghost !py-2 !px-3 rounded-full" aria-label="Избранное">
            <Heart className="w-4 h-4" />
          </Link>
          {loading ? (
            <div className="w-9 h-9 rounded-full bg-[var(--color-surface-2)] animate-pulse" />
          ) : user ? (
            <div className="flex items-center gap-2">
              <div className="hidden md:block text-right">
                <div className="text-xs text-[var(--color-ink-4)] leading-none">{user.display_name ?? "Привет"}</div>
                <div className="text-xs text-[var(--color-ink-3)] truncate max-w-[140px]">{user.email}</div>
              </div>
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
                <UserIcon className="w-4 h-4" /> Войти
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
