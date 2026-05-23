"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ProductCard } from "@/components/ProductCard";
import { GridSkeleton } from "@/components/Skeleton";
import { api, getToken } from "@/lib/api";
import type { Favorite, ProductOffer, Source } from "@/lib/types";

function toOffer(f: Favorite): ProductOffer {
  return {
    source: f.source as Source,
    name: f.name, price: f.price, currency: f.currency, url: f.url, image: f.image,
    images: f.image ? [f.image] : [],
    characteristics: {}, seller: null, rating: null,
    reviews: [], reviews_count: null,
    fetched_at: f.added_at, cached: false,
  };
}

export default function FavoritesPage() {
  const [items, setItems] = useState<Favorite[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) { setErr("auth-required"); return; }
    api.favorites.list().then(setItems).catch((e) => setErr(String(e?.message ?? e)));
  }, []);

  if (err === "auth-required") {
    return (
      <div className="card p-12 text-center max-w-md mx-auto">
        <div className="text-3xl">🔒</div>
        <h1 className="text-xl font-semibold mt-3">Нужен аккаунт</h1>
        <p className="text-sm text-[var(--color-ink-4)] mt-1">Чтобы сохранять понравившиеся товары — войдите или зарегистрируйтесь.</p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <Link href="/login" className="btn btn-ghost">Войти</Link>
          <Link href="/register" className="btn btn-primary">Создать аккаунт</Link>
        </div>
      </div>
    );
  }

  if (err) {
    return <div className="card p-6 text-sm text-amber-700 bg-amber-50">{err}</div>;
  }

  if (!items) return <GridSkeleton count={6} />;

  if (items.length === 0) {
    return (
      <div className="card p-12 text-center">
        <div className="text-3xl">🤍</div>
        <h1 className="text-xl font-semibold mt-3">Тут пока пусто</h1>
        <p className="text-sm text-[var(--color-ink-4)] mt-1">Жмите ♥ на любом товаре — он появится здесь.</p>
        <Link href="/" className="btn btn-primary mt-5 inline-flex">Назад к поиску</Link>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Избранное</h1>
      <p className="text-sm text-[var(--color-ink-4)] mt-1">{items.length} товаров</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-6">
        {items.map((f, i) => (
          <ProductCard key={f.id} offer={toOffer(f)} index={i} />
        ))}
      </div>
    </div>
  );
}
