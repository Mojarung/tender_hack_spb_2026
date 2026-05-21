"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ProductCard } from "@/components/ProductCard";
import { api } from "@/lib/api";
import type { Favorite, ProductOffer, Source } from "@/lib/types";

function toOffer(f: Favorite): ProductOffer {
  return {
    source: f.source as Source,
    name: f.name,
    price: f.price,
    currency: f.currency,
    url: f.url,
    image: f.image,
    characteristics: {},
    seller: null,
    rating: null,
    fetched_at: f.added_at,
    cached: false,
  };
}

export default function FavoritesPage() {
  const [items, setItems] = useState<Favorite[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.favorites.list().then(setItems).catch((e) => setErr(String(e)));
  }, []);

  if (err) {
    return (
      <div className="card p-8 text-center">
        <p className="text-[var(--color-ink-700)] mb-3">Нужна авторизация, чтобы видеть избранное.</p>
        <Link href="/login" className="btn-primary inline-block">Войти</Link>
      </div>
    );
  }

  if (!items) return <div className="text-[var(--color-ink-500)]">Загружаю…</div>;

  if (items.length === 0)
    return (
      <div className="card p-12 text-center">
        <div className="text-2xl">📭</div>
        <p className="mt-2 text-[var(--color-ink-700)] font-semibold">Пока тут пусто</p>
        <p className="mt-1 text-[var(--color-ink-400)]">Жмите ♥ на товаре чтобы сохранить.</p>
      </div>
    );

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-1">Избранное</h1>
      <p className="text-sm text-[var(--color-ink-500)]">{items.length} товаров</p>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 mt-6">
        {items.map((f) => (
          <ProductCard key={f.id} offer={toOffer(f)} />
        ))}
      </div>
    </div>
  );
}
