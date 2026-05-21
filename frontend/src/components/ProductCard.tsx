"use client";

import { Heart, Star } from "lucide-react";
import { useState } from "react";
import clsx from "clsx";

import { api } from "@/lib/api";
import type { ProductOffer } from "@/lib/types";

const SOURCE_LABEL: Record<string, string> = {
  wb: "WB",
  ozon: "Ozon",
  ya_market: "Маркет",
  runet: "Рунет",
};

function formatPrice(p: string, currency: string) {
  const n = Number(p);
  if (!Number.isFinite(n)) return p;
  const formatted = n.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  return currency === "RUB" ? `${formatted} ₽` : `${formatted} ${currency}`;
}

function itemIdFromUrl(o: ProductOffer): string {
  if (o.source === "wb") {
    const m = o.url.match(/catalog\/(\d+)/);
    return m?.[1] ?? "";
  }
  return o.url;
}

export function ProductCard({ offer }: { offer: ProductOffer }) {
  const [fav, setFav] = useState(false);
  const [busy, setBusy] = useState(false);

  async function toggleFav() {
    if (busy) return;
    setBusy(true);
    try {
      await api.favorites.add({
        source: offer.source,
        item_id: itemIdFromUrl(offer),
        name: offer.name,
        price: offer.price,
        currency: offer.currency,
        url: offer.url,
        image: offer.image,
      });
      setFav(true);
    } catch {
      window.location.href = "/login";
    } finally {
      setBusy(false);
    }
  }

  const reviews = offer.characteristics?.feedbacks;
  const rating = offer.rating ?? Number(offer.characteristics?.rating ?? 0);

  return (
    <div className="card p-5 flex flex-col gap-3 h-full">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className={clsx("badge-source", `badge-source-${offer.source}`)}>
            {SOURCE_LABEL[offer.source] ?? offer.source}
          </div>
          <h3 className="mt-2 text-base font-semibold text-[var(--color-ink-900)] truncate">
            {offer.name}
          </h3>
          {offer.seller && (
            <p className="text-xs text-[var(--color-ink-400)] mt-0.5">{offer.seller}</p>
          )}
        </div>
        <button
          onClick={toggleFav}
          aria-label="В избранное"
          className="p-2 -m-2 disabled:opacity-50"
          disabled={busy}
        >
          <Heart
            className={clsx(
              "w-5 h-5 transition-colors",
              fav ? "fill-[var(--color-error)] stroke-[var(--color-error)]" : "stroke-[var(--color-ink-400)]",
            )}
          />
        </button>
      </div>

      {offer.image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={offer.image}
          alt={offer.name}
          loading="lazy"
          className="h-36 object-contain w-full"
        />
      ) : (
        <div className="h-36 grid place-items-center bg-[var(--color-ink-50)] rounded-md text-[var(--color-ink-400)] text-xs">
          без фото
        </div>
      )}

      <div className="flex items-center gap-3 text-xs text-[var(--color-ink-500)]">
        {rating > 0 && (
          <span className="flex items-center gap-1">
            <Star className="w-3.5 h-3.5 fill-[var(--color-warning)] stroke-[var(--color-warning)]" />
            {rating.toFixed(1)}
          </span>
        )}
        {reviews && <span>{reviews} отзывов</span>}
        {offer.cached && <span className="text-[var(--color-brand-500)]">из кэша</span>}
      </div>

      <div className="flex items-end justify-between mt-auto pt-2">
        <div>
          <div className="text-xl font-bold text-[var(--color-ink-900)]">
            {formatPrice(offer.price, offer.currency)}
          </div>
        </div>
        <a
          href={offer.url}
          target="_blank"
          rel="noopener noreferrer"
          className="btn-primary text-sm !py-2 !px-3"
        >
          К товару
        </a>
      </div>
    </div>
  );
}
