"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Bell, Heart, Search, Settings, SlidersHorizontal, User as UserIcon } from "lucide-react";

import { api } from "@/lib/api";

export function Header() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [user, setUser] = useState<{ email: string; display_name?: string | null } | null>(null);

  useEffect(() => {
    api.auth.me().then((u) => setUser(u)).catch(() => setUser(null));
  }, []);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    router.push(`/search?q=${encodeURIComponent(q.trim())}`);
  }

  return (
    <header className="bg-white border-b border-[var(--color-ink-100)]">
      <div className="max-w-[1240px] mx-auto px-6 py-4 flex items-center gap-6">
        <Link href="/" className="text-2xl font-bold text-[var(--color-brand-500)] tracking-tight">
          PricePulse
        </Link>

        <form onSubmit={submit} className="flex-1 max-w-[600px] relative">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-[var(--color-ink-400)]" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Поиск товара по маркетплейсам..."
            className="w-full pl-12 pr-12 py-3 rounded-[40px] border border-[var(--color-ink-200)]
                       focus:outline-none focus:border-[var(--color-brand-400)] bg-white"
          />
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 p-2 rounded-md hover:bg-[var(--color-ink-50)]"
          >
            <SlidersHorizontal className="w-4 h-4 text-[var(--color-ink-500)]" />
          </button>
        </form>

        <nav className="flex items-center gap-2">
          <Link
            href="/favorites"
            className="p-3 rounded-full border border-[var(--color-ink-100)] hover:border-[var(--color-brand-400)] transition-colors"
            aria-label="Избранное"
          >
            <Heart className="w-5 h-5 text-[var(--color-ink-700)]" />
          </Link>
          <button
            className="relative p-3 rounded-full border border-[var(--color-ink-100)] hover:border-[var(--color-brand-400)]"
            aria-label="Уведомления"
          >
            <Bell className="w-5 h-5 text-[var(--color-ink-700)]" />
            <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-[var(--color-error)]" />
          </button>
          <button
            className="p-3 rounded-full border border-[var(--color-ink-100)] hover:border-[var(--color-brand-400)]"
            aria-label="Настройки"
          >
            <Settings className="w-5 h-5 text-[var(--color-ink-700)]" />
          </button>
          {user ? (
            <Link
              href="/favorites"
              className="ml-2 w-11 h-11 rounded-full bg-[var(--color-brand-500)] text-white flex items-center justify-center font-semibold"
            >
              {(user.display_name || user.email)[0]?.toUpperCase()}
            </Link>
          ) : (
            <Link href="/login" className="ml-2 btn-primary !rounded-full">
              <span className="flex items-center gap-2"><UserIcon className="w-4 h-4" /> Войти</span>
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
