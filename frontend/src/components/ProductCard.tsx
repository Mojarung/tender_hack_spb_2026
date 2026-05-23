"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { ArrowUpRight, Heart, Star, Truck } from "lucide-react";
import { useState } from "react";
import toast from "react-hot-toast";

import { api, getToken, proxyImage } from "@/lib/api";
import { formatPrice, itemIdFromOffer } from "@/lib/format";
import { SOURCE_LABEL, type ProductOffer } from "@/lib/types";

const sourceClass: Record<string, string> = {
  wb: "source-dot-wb",
  ozon: "source-dot-ozon",
  ya_market: "source-dot-ya_market",
  runet: "source-dot-runet",
};

const colorLabel: Record<string, string> = {
  black: "черный",
  white: "белый",
  blue: "синий",
  red: "красный",
  pink: "розовый",
  green: "зеленый",
  yellow: "желтый",
  gray: "серый",
  silver: "серебристый",
  gold: "золотой",
  purple: "фиолетовый",
  orange: "оранжевый",
};

function attributeChips(offer: ProductOffer): string[] {
  const a = offer.attributes;
  if (!a) return [];
  return [
    a.model,
    a.ram_gb ? `${a.ram_gb} ГБ RAM` : null,
    a.storage_gb ? `${a.storage_gb} ГБ` : null,
    a.color ? colorLabel[a.color] ?? a.color : null,
    a.size,
    a.season,
    a.paper_format,
    a.density_gm2 ? `${a.density_gm2} г/м²` : null,
    a.sheets_count ? `${a.sheets_count} л.` : null,
  ].filter((v): v is string => !!v).slice(0, 3);
}

function deliveryText(offer: ProductOffer): string | null {
  const d = offer.delivery;
  if (!d) return null;
  if (d.delivery_text) return d.delivery_text;
  if (d.eta_max_hours == null) return null;
  const city = d.city ? `${d.city}: ` : "";
  const min = d.eta_min_hours != null ? `${d.eta_min_hours}-` : "до ";
  return `${city}${min}${d.eta_max_hours} ч`;
}

interface Props {
  offer: ProductOffer;
  index?: number;
  highlight?: boolean;   // best deal accent
}

export function ProductCard({ offer, index = 0, highlight }: Props) {
  const [fav, setFav] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imgFailed, setImgFailed] = useState(false);

  const rating = offer.rating ?? Number(offer.characteristics?.rating ?? 0);
  const feedbacks = offer.characteristics?.feedbacks;
  const chips = attributeChips(offer);
  const delivery = deliveryText(offer);

  async function toggleFav(e: React.MouseEvent) {
    e.preventDefault();
    if (busy) return;
    if (!getToken()) {
      toast("Сначала войдите, чтобы сохранять", { icon: "🔒" });
      return;
    }
    setBusy(true);
    try {
      await api.favorites.add({
        source: offer.source,
        item_id: itemIdFromOffer(offer),
        name: offer.name,
        price: offer.price,
        currency: offer.currency,
        url: offer.url,
        image: offer.image,
      });
      setFav(true);
      toast.success("Добавлено в избранное");
    } catch (err) {
      toast.error(err instanceof Error ? err.message.slice(0, 80) : "не вышло");
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.a
      href={offer.url}
      target="_blank"
      rel="noopener noreferrer"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.04, 0.4), duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className={clsx(
        "card card-hover p-4 flex flex-col gap-3 h-full group relative",
        highlight && "ring-1 ring-[var(--color-accent-100)]"
      )}
    >
      {/* Source + favorite */}
      <div className="flex items-center justify-between">
        <div className="chip">
          <span className={clsx("source-dot", sourceClass[offer.source])} />
          {SOURCE_LABEL[offer.source]}
        </div>
        <button
          onClick={toggleFav}
          disabled={busy}
          aria-label="В избранное"
          className="p-1.5 -m-1.5 rounded-full hover:bg-[var(--color-surface-2)] transition-colors disabled:opacity-50"
        >
          <Heart
            className={clsx(
              "w-4 h-4 transition-all",
              fav
                ? "fill-[var(--color-bad)] stroke-[var(--color-bad)] scale-110"
                : "stroke-[var(--color-ink-4)] group-hover:stroke-[var(--color-ink-2)]"
            )}
          />
        </button>
      </div>

      {/* Image */}
      <div className="h-32 grid place-items-center bg-[var(--color-surface-2)] rounded-lg overflow-hidden">
        {offer.image && !imgFailed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={proxyImage(offer.image, offer.source)}
            alt={offer.name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="h-full max-w-full object-contain transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="text-[var(--color-ink-4)] text-3xl font-semibold tracking-tight">
            {offer.name.slice(0, 2).toUpperCase()}
          </div>
        )}
      </div>

      {/* Title + meta */}
      <div className="flex-1 min-h-0">
        <h3 className="text-sm font-semibold leading-snug line-clamp-2 text-[var(--color-ink)]">
          {offer.name}
        </h3>
        <div className="mt-1.5 flex items-center gap-2 text-xs text-[var(--color-ink-4)]">
          {rating > 0 && (
            <span className="inline-flex items-center gap-1">
              <Star className="w-3 h-3 fill-[var(--color-warn)] stroke-[var(--color-warn)]" />
              {rating.toFixed(1)}
            </span>
          )}
          {feedbacks && <span>· {feedbacks} отзывов</span>}
          {offer.seller && <span className="truncate">· {offer.seller}</span>}
        </div>
        {chips.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {chips.map((chip) => (
              <span key={chip} className="chip !px-2 !py-0.5 text-[11px]">
                {chip}
              </span>
            ))}
          </div>
        )}
        {delivery && (
          <div className="mt-2 inline-flex items-center gap-1 text-[11px] text-[var(--color-ink-4)]">
            <Truck className="w-3 h-3" />
            {delivery}
          </div>
        )}
      </div>

      {/* Price + CTA */}
      <div className="flex items-end justify-between pt-1">
        <div className="text-lg font-semibold tabular-nums">
          {formatPrice(offer.price, offer.currency)}
        </div>
        <span className="text-xs text-[var(--color-ink-4)] inline-flex items-center gap-1 group-hover:text-[var(--color-ink-2)] transition-colors">
          К товару <ArrowUpRight className="w-3 h-3" />
        </span>
      </div>
    </motion.a>
  );
}
