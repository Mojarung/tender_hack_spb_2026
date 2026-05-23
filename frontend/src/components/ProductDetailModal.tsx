"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUpRight, ChevronLeft, ChevronRight, Star, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { proxyImage } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { SOURCE_LABEL, type ProductOffer, type ProductReview } from "@/lib/types";

type ReviewSort = "newest" | "oldest" | "rating_desc" | "rating_asc";

const SORT_LABEL: Record<ReviewSort, string> = {
  newest: "Сначала новые",
  oldest: "Сначала старые",
  rating_desc: "Высокий рейтинг",
  rating_asc: "Низкий рейтинг",
};

/** Best-effort timestamp parser — accepts epoch sec/ms, ISO, or human RU.
 *  Returns NaN for unparseable strings so callers can fall back to a stable order. */
function reviewTimestamp(raw: string | null | undefined): number {
  if (!raw) return Number.NaN;
  const asNum = Number(raw);
  if (Number.isFinite(asNum) && asNum > 1e9) {
    return asNum < 1e12 ? asNum * 1000 : asNum;
  }
  return Date.parse(raw);
}

function sortReviews(list: ProductReview[], by: ReviewSort): ProductReview[] {
  // Always work on a copy — `list` is frozen Pydantic-derived data.
  const copy = list.slice();
  // Stable order via a tiebreaker on the original index so ties don't flicker.
  const indexed = copy.map((r, i) => ({ r, i }));
  indexed.sort((a, b) => {
    let cmp = 0;
    if (by === "newest" || by === "oldest") {
      const ta = reviewTimestamp(a.r.published_at);
      const tb = reviewTimestamp(b.r.published_at);
      // NaN goes last regardless of direction
      const aNan = Number.isNaN(ta);
      const bNan = Number.isNaN(tb);
      if (aNan && !bNan) return 1;
      if (!aNan && bNan) return -1;
      if (!aNan && !bNan) cmp = by === "newest" ? tb - ta : ta - tb;
    } else {
      const sa = a.r.score ?? -1;
      const sb = b.r.score ?? -1;
      cmp = by === "rating_desc" ? sb - sa : sa - sb;
    }
    return cmp !== 0 ? cmp : a.i - b.i;
  });
  return indexed.map(({ r }) => r);
}

const sourceClass: Record<string, string> = {
  wb: "source-dot-wb",
  ozon: "source-dot-ozon",
  ya_market: "source-dot-ya_market",
  runet: "source-dot-runet",
};

const RU_DATE = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric", month: "long", year: "numeric",
});

/** Render Ozon's published_at — accepts ISO, "DD.MM.YYYY", or raw text */
function formatReviewDate(raw: string): string {
  // Try epoch (sec or ms)
  const asNum = Number(raw);
  if (Number.isFinite(asNum) && asNum > 1e9) {
    const ms = asNum < 1e12 ? asNum * 1000 : asNum;
    return RU_DATE.format(new Date(ms));
  }
  // Try Date.parse for ISO
  const ts = Date.parse(raw);
  if (!Number.isNaN(ts)) return RU_DATE.format(new Date(ts));
  // Already human-readable (e.g. "12 мая 2025")
  return raw;
}

interface Props {
  offer: ProductOffer | null;
  onClose: () => void;
}

export function ProductDetailModal({ offer, onClose }: Props) {
  const [activeImage, setActiveImage] = useState(0);
  const [mounted, setMounted] = useState(false);
  const [reviewSort, setReviewSort] = useState<ReviewSort>("newest");

  useEffect(() => setMounted(true), []);

  // Reset gallery position + sort whenever a new product opens.
  useEffect(() => {
    setActiveImage(0);
    setReviewSort("newest");
  }, [offer?.url]);

  // Esc closes; lock body scroll while open.
  useEffect(() => {
    if (!offer) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") setActiveImage((i) => Math.max(0, i - 1));
      if (e.key === "ArrowRight")
        setActiveImage((i) => Math.min((offer.images?.length || 1) - 1, i + 1));
    };
    document.addEventListener("keydown", onKey);
    const origOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = origOverflow;
    };
  }, [offer, onClose]);

  // ALL hooks must run on every render in the same order (Rules of Hooks),
  // so compute these before the `!mounted` early-return below.
  const reviews = offer?.reviews ?? [];
  const sortedReviews = useMemo(
    () => sortReviews(reviews, reviewSort),
    [reviews, reviewSort],
  );

  if (!mounted) return null;

  // `images` may be empty — fall back to single `image` so the modal
  // still renders something useful.
  const images = offer
    ? offer.images && offer.images.length > 0
      ? offer.images
      : offer.image
        ? [offer.image]
        : []
    : [];

  const rating =
    (offer?.rating ?? Number(offer?.characteristics?.rating ?? 0)) || 0;
  const chars = Object.entries(offer?.characteristics ?? {}).filter(
    ([k]) => !["rating", "feedbacks", "reviews", "seller"].includes(k),
  );

  return createPortal(
    <AnimatePresence>
      {offer && (
        <motion.div
          key="overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={onClose}
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm grid place-items-center p-4 overflow-y-auto"
        >
          <motion.div
            key="modal"
            initial={{ opacity: 0, scale: 0.96, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 6 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            onClick={(e) => e.stopPropagation()}
            className="card w-full max-w-5xl max-h-[90vh] grid grid-rows-[auto_1fr_auto] overflow-hidden relative my-auto"
          >
            {/* Header */}
            <div className="flex items-start justify-between gap-4 p-5 border-b border-[var(--color-border)]">
              <div className="min-w-0 flex-1">
                <div className="chip mb-2">
                  <span className={clsx("source-dot", sourceClass[offer.source])} />
                  {SOURCE_LABEL[offer.source]}
                </div>
                <h2 className="text-lg font-semibold leading-snug text-[var(--color-ink)]">
                  {offer.name}
                </h2>
                <div className="mt-1 flex items-center gap-2 text-sm text-[var(--color-ink-4)]">
                  {rating > 0 && (
                    <span className="inline-flex items-center gap-1">
                      <Star className="w-3.5 h-3.5 fill-[var(--color-warn)] stroke-[var(--color-warn)]" />
                      {rating.toFixed(1)}
                    </span>
                  )}
                  {offer.reviews_count != null && (
                    <span>· {offer.reviews_count} отзывов</span>
                  )}
                  {offer.seller && <span className="truncate">· {offer.seller}</span>}
                </div>
              </div>
              <button
                onClick={onClose}
                aria-label="Закрыть"
                className="p-2 -m-2 rounded-full hover:bg-[var(--color-surface-2)] transition-colors"
              >
                <X className="w-5 h-5 stroke-[var(--color-ink-3)]" />
              </button>
            </div>

            {/* Body — gallery left, details right */}
            <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-5 p-5 overflow-y-auto">
              {/* Gallery */}
              <div className="flex flex-col gap-3 min-w-0">
                <div className="relative aspect-[3/4] bg-[var(--color-surface-2)] rounded-xl overflow-hidden grid place-items-center">
                  {images[activeImage] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={proxyImage(images[activeImage], offer.source)}
                      alt={`${offer.name} (${activeImage + 1}/${images.length})`}
                      className="h-full max-w-full object-contain"
                    />
                  ) : (
                    <div className="text-[var(--color-ink-4)] text-5xl font-semibold">
                      {offer.name.slice(0, 2).toUpperCase()}
                    </div>
                  )}

                  {images.length > 1 && (
                    <>
                      <button
                        onClick={() =>
                          setActiveImage((i) => Math.max(0, i - 1))
                        }
                        disabled={activeImage === 0}
                        aria-label="Предыдущее фото"
                        className="absolute left-2 top-1/2 -translate-y-1/2 p-1.5 rounded-full bg-[var(--color-surface)]/80 hover:bg-[var(--color-surface)] backdrop-blur transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <ChevronLeft className="w-5 h-5" />
                      </button>
                      <button
                        onClick={() =>
                          setActiveImage((i) =>
                            Math.min(images.length - 1, i + 1),
                          )
                        }
                        disabled={activeImage === images.length - 1}
                        aria-label="Следующее фото"
                        className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-full bg-[var(--color-surface)]/80 hover:bg-[var(--color-surface)] backdrop-blur transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <ChevronRight className="w-5 h-5" />
                      </button>
                      <div className="absolute bottom-2 right-2 text-xs text-[var(--color-ink-3)] bg-[var(--color-surface)]/80 backdrop-blur px-2 py-0.5 rounded-full">
                        {activeImage + 1} / {images.length}
                      </div>
                    </>
                  )}
                </div>

                {images.length > 1 && (
                  <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
                    {images.map((src, i) => (
                      <button
                        key={src + i}
                        onClick={() => setActiveImage(i)}
                        className={clsx(
                          "shrink-0 w-16 h-16 rounded-lg overflow-hidden border-2 transition-all bg-[var(--color-surface-2)] grid place-items-center",
                          i === activeImage
                            ? "border-[var(--color-accent-100)]"
                            : "border-transparent opacity-60 hover:opacity-100",
                        )}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={proxyImage(src, offer.source)}
                          alt={`Фото ${i + 1}`}
                          className="h-full max-w-full object-contain"
                          loading="lazy"
                        />
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Details */}
              <div className="flex flex-col gap-5 min-w-0">
                {/* Characteristics */}
                <section>
                  <h3 className="text-sm font-semibold text-[var(--color-ink-2)] mb-2">
                    Характеристики
                  </h3>
                  {chars.length > 0 ? (
                    <dl className="grid grid-cols-1 gap-1.5 text-sm">
                      {chars.map(([name, value]) => (
                        <div
                          key={name}
                          className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] gap-3 py-1.5 border-b border-[var(--color-border)] last:border-0"
                        >
                          <dt className="text-[var(--color-ink-4)] truncate">
                            {name}
                          </dt>
                          <dd className="text-[var(--color-ink)] break-words">
                            {value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="text-sm text-[var(--color-ink-4)]">
                      Характеристики не указаны.
                    </p>
                  )}
                </section>

                {/* Reviews */}
                <section>
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <h3 className="text-sm font-semibold text-[var(--color-ink-2)]">
                      Отзывы {reviews.length > 0 && (
                        <span className="text-[var(--color-ink-4)] font-normal">
                          ({reviews.length})
                        </span>
                      )}
                    </h3>
                    {reviews.length > 1 && (
                      <select
                        value={reviewSort}
                        onChange={(e) => setReviewSort(e.target.value as ReviewSort)}
                        aria-label="Сортировка отзывов"
                        className="text-xs px-2 py-1 rounded-md bg-[var(--color-surface-2)] text-[var(--color-ink-2)] border border-[var(--color-border)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-100)]"
                      >
                        {(Object.keys(SORT_LABEL) as ReviewSort[]).map((k) => (
                          <option key={k} value={k}>{SORT_LABEL[k]}</option>
                        ))}
                      </select>
                    )}
                  </div>
                  {reviews.length > 0 ? (
                    <ul className="flex flex-col gap-3 max-h-[420px] overflow-y-auto pr-1">
                      {sortedReviews.map((r, i) => (
                        <li
                          key={i}
                          className="p-3 rounded-lg bg-[var(--color-surface-2)]"
                        >
                          <div className="flex items-center justify-between text-xs text-[var(--color-ink-4)] mb-1.5">
                            <span className="font-medium text-[var(--color-ink-2)] truncate">
                              {r.author || "Аноним"}
                              {r.published_at && (
                                <span className="ml-2 font-normal text-[var(--color-ink-4)]">
                                  · {formatReviewDate(r.published_at)}
                                </span>
                              )}
                            </span>
                            {r.score != null && (
                              <span className="inline-flex items-center gap-1 shrink-0">
                                <Star className="w-3 h-3 fill-[var(--color-warn)] stroke-[var(--color-warn)]" />
                                {r.score}
                              </span>
                            )}
                          </div>
                          <p className="text-sm text-[var(--color-ink)] leading-relaxed whitespace-pre-line">
                            {r.text}
                          </p>
                          {r.photos && r.photos.length > 0 && (
                            <div className="mt-2 flex gap-1.5 flex-wrap">
                              {r.photos.map((src, pi) => (
                                <a
                                  key={src + pi}
                                  href={src}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="block w-14 h-14 rounded-md overflow-hidden bg-[var(--color-surface)] hover:ring-2 hover:ring-[var(--color-accent-100)] transition-all"
                                >
                                  {/* eslint-disable-next-line @next/next/no-img-element */}
                                  <img
                                    src={proxyImage(src, offer.source)}
                                    alt={`Фото отзыва ${pi + 1}`}
                                    loading="lazy"
                                    className="h-full w-full object-cover"
                                  />
                                </a>
                              ))}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-[var(--color-ink-4)]">
                      Отзывов пока нет.
                    </p>
                  )}
                </section>
              </div>
            </div>

            {/* Footer — price + external link */}
            <div className="flex items-center justify-between gap-4 p-5 border-t border-[var(--color-border)]">
              <div className="text-2xl font-semibold tabular-nums">
                {formatPrice(offer.price, offer.currency)}
              </div>
              <a
                href={offer.url}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-primary inline-flex items-center gap-1.5"
              >
                Открыть на {SOURCE_LABEL[offer.source]}
                <ArrowUpRight className="w-4 h-4" />
              </a>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
