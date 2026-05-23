"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { Eye, Heart, Star } from "lucide-react";
import { useState } from "react";
import toast from "react-hot-toast";

import { ProductDetailModal } from "@/components/ProductDetailModal";
import { api, getToken, proxyImage } from "@/lib/api";
import { formatPrice, itemIdFromOffer } from "@/lib/format";
import { SOURCE_LABEL, type ProductOffer } from "@/lib/types";

const sourceClass: Record<string, string> = {
  wb: "source-dot-wb",
  ozon: "source-dot-ozon",
  ya_market: "source-dot-ya_market",
  runet: "source-dot-runet",
};

interface Props {
  offer: ProductOffer;
  index?: number;
  highlight?: boolean;   // best deal accent
}

export function ProductCard({ offer, index = 0, highlight }: Props) {
  const [fav, setFav] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imgFailed, setImgFailed] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  const rating = offer.rating ?? Number(offer.characteristics?.rating ?? 0);
  const feedbacks =
    offer.reviews_count != null
      ? String(offer.reviews_count)
      : offer.characteristics?.feedbacks;

  async function toggleFav(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();    // never bubble into the card-click → modal
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
    <>
      <motion.button
        type="button"
        onClick={() => setModalOpen(true)}
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: Math.min(index * 0.04, 0.4), duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        className={clsx(
          "card card-hover p-4 flex flex-col gap-3 h-full group relative text-left w-full",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-100)]",
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
            type="button"
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

        {/* Image — 3:4 portrait plate, WB-style */}
        <div className="aspect-[3/4] w-full grid place-items-center bg-[var(--color-surface-2)] rounded-lg overflow-hidden">
          {offer.image && !imgFailed ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={proxyImage(offer.image, offer.source)}
              alt={offer.name}
              loading="lazy"
              onError={() => setImgFailed(true)}
              className="w-full h-full object-contain transition-transform duration-300 group-hover:scale-105"
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
            {offer.rerank_score != null && (
              <span
                className="ml-auto shrink-0 inline-flex items-center gap-0.5 font-medium tabular-nums"
                style={{ color: offer.rerank_score >= 0.7 ? "var(--color-good, #22c55e)" : offer.rerank_score >= 0.4 ? "var(--color-warn, #f59e0b)" : "var(--color-ink-4)" }}
                title={`Релевантность запросу: ${Math.round(offer.rerank_score * 100)}%`}
              >
                ↑{Math.round(offer.rerank_score * 100)}%
              </span>
            )}
          </div>
        </div>

        {/* Price + CTA */}
        <div className="flex items-end justify-between pt-1">
          <div className="text-lg font-semibold tabular-nums">
            {formatPrice(offer.price, offer.currency)}
          </div>
          <span className="text-xs text-[var(--color-ink-4)] inline-flex items-center gap-1 group-hover:text-[var(--color-ink-2)] transition-colors">
            Подробнее <Eye className="w-3 h-3" />
          </span>
        </div>
      </motion.button>

      <ProductDetailModal
        offer={modalOpen ? offer : null}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
}
