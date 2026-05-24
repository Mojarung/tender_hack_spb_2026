"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { Eye, Heart, Star } from "lucide-react";
import { useState } from "react";
import toast from "react-hot-toast";

import { PriceSparkline } from "@/components/PriceSparkline";
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
  query?: string;        // forwarded to the AI explainer
  allOffers?: ProductOffer[];   // forwarded to the AI explainer for context
  /** Median price within the same SourceGroup — used to flag offers
   *  that fall sufficiently below as «ДЕМПИНГ» per 44-ФЗ ст.37 (the
   *  buyer must request extra performance bond from such a supplier). */
  groupMedian?: number;
  /** Total offer count in the source group — we require ≥5 before
   *  trusting the median (small Runet groups with 2-3 items produce
   *  noisy medians that flagged ordinary prices as dumping). */
  groupOfferCount?: number;
}

export function ProductCard({ offer, index = 0, highlight, query, allOffers, groupMedian, groupOfferCount }: Props) {
  const [dumpExplain, setDumpExplain] = useState(false);
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
          <div className="flex items-center gap-1.5 flex-wrap">
            <div className="chip">
              <span className={clsx("source-dot", sourceClass[offer.source])} />
              {SOURCE_LABEL[offer.source]}
            </div>
            {/* «ДЕМПИНГ −X%» — when price drops ≥25 % below the source
                median. Triggers anti-dumping clause (44-ФЗ ст.37) —
                the buyer must request 1.5× performance bond from such
                a supplier. Hover for the explanation. */}
            {(() => {
              const price = Number(offer.price);
              if (!groupMedian || !(price > 0)) return null;
              // Need a statistically meaningful median — small groups
              // (typical of niche Runet queries) produce wild swings.
              if (!groupOfferCount || groupOfferCount < 5) return null;
              const dropPct = Math.round(((groupMedian - price) / groupMedian) * 100);
              // Runet aggregates real retail shops (DNS / М.Видео /
              // Эльдорадо) whose legitimate prices vary by 20-25%.
              // Bump the threshold for Runet to avoid false flags.
              const threshold = offer.source === "runet" ? 35 : 30;
              if (dropPct < threshold) return null;
              const medianStr = Math.round(groupMedian).toLocaleString("ru-RU");
              return (
                <span className="relative inline-block">
                  <button
                    type="button"
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); setDumpExplain((v) => !v); }}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-50 text-red-700 border border-red-200 hover:bg-red-100 transition-colors cursor-pointer"
                  >
                    ⚠ ДЕМПИНГ −{dropPct}%
                  </button>
                  {dumpExplain && (
                    <div
                      onClick={(e) => e.stopPropagation()}
                      className="absolute z-30 mt-1 left-0 w-72 p-3 rounded-lg border border-red-200 bg-white shadow-lg text-[11px] leading-relaxed text-[var(--color-ink-2)]"
                    >
                      <div className="font-semibold text-red-700 mb-1.5">
                        Почему здесь демпинг
                      </div>
                      <div className="space-y-1">
                        <div>
                          <span className="text-[var(--color-ink-4)]">Цена этого товара:</span>{" "}
                          <span className="font-semibold tabular-nums">
                            {Math.round(price).toLocaleString("ru-RU")} ₽
                          </span>
                        </div>
                        <div>
                          <span className="text-[var(--color-ink-4)]">Средняя по магазину:</span>{" "}
                          <span className="font-semibold tabular-nums">{medianStr} ₽</span>
                        </div>
                        <div>
                          <span className="text-[var(--color-ink-4)]">Разница:</span>{" "}
                          <span className="font-semibold text-red-700">−{dropPct}%</span>
                          {" "}(порог {threshold}%)
                        </div>
                      </div>
                      <p className="mt-2 text-[var(--color-ink-3)]">
                        По 44-ФЗ ст.37 если цена победителя ниже НМЦК на 25%,
                        нужно повышенное обеспечение контракта (1.5×) или
                        подтверждение добросовестности.
                      </p>
                      <button
                        type="button"
                        onClick={() => setDumpExplain(false)}
                        className="mt-2 text-[10px] text-[var(--color-ink-4)] hover:text-[var(--color-ink-2)]"
                      >
                        Закрыть
                      </button>
                    </div>
                  )}
                </span>
              );
            })()}
            {/* «X% совпадение» — query↔offer similarity from the
                backend's relevance scorer (name + key chars). Coloured
                green for ≥80, neutral for 60-79, muted for the rest
                so the user can spot exact matches at a glance. */}
            {(() => {
              const rel = Number(offer.relevance ?? NaN);
              if (!Number.isFinite(rel) || rel <= 0) return null;
              const pct = Math.round(rel);
              const color = pct >= 80
                ? "bg-[color-mix(in_srgb,var(--color-good)_14%,white)] text-[var(--color-good)] border-[color-mix(in_srgb,var(--color-good)_30%,transparent)]"
                : pct >= 60
                  ? "bg-[var(--color-accent-50)] text-[var(--color-accent-2)] border-[var(--color-accent-100)]"
                  : "bg-[var(--color-surface-2)] text-[var(--color-ink-4)] border-[var(--color-border)]";
              return (
                <span
                  title="Насколько товар совпадает с вашим запросом (по названию и характеристикам)"
                  className={clsx(
                    "inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium border tabular-nums",
                    color,
                  )}
                >
                  {pct}% совпадение
                </span>
              );
            })()}
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
          </div>
        </div>

        {/* Price + sparkline + CTA */}
        <div className="flex items-end justify-between pt-1 gap-2">
          <div className="min-w-0">
            <div className="text-lg font-semibold tabular-nums">
              {formatPrice(offer.price, offer.currency)}
            </div>
            <PriceSparkline offer={offer} />
          </div>
          <span className="text-xs text-[var(--color-ink-4)] inline-flex items-center gap-1 group-hover:text-[var(--color-ink-2)] transition-colors shrink-0">
            Подробнее <Eye className="w-3 h-3" />
          </span>
        </div>
      </motion.button>

      <ProductDetailModal
        offer={modalOpen ? offer : null}
        onClose={() => setModalOpen(false)}
        query={query}
        allOffers={allOffers}
      />
    </>
  );
}
