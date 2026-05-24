"use client";

import { useEffect, useState } from "react";
import { Line, LineChart, ResponsiveContainer } from "recharts";

import { api } from "@/lib/api";
import { itemIdFromOffer } from "@/lib/format";
import type { ProductOffer } from "@/lib/types";

/** Tiny inline price-history chart under the price. Fetches lazily on
 *  mount, renders nothing while loading, fades in once data arrives.
 *  Empty / single-point history is invisible — we don't show "1 dot"
 *  fake history that would mislead the user. */
export function PriceSparkline({ offer }: { offer: ProductOffer }) {
  const [points, setPoints] = useState<{ ts: string; price: number }[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.priceHistory(offer.source, itemIdFromOffer(offer), 60);
        if (cancelled) return;
        const parsed = res.points
          .map((p) => ({ ts: p.ts, price: Number(p.price) }))
          .filter((p) => Number.isFinite(p.price) && p.price > 0)
          // backend returns latest-first; reverse for left-to-right time
          .reverse();
        setPoints(parsed);
      } catch {
        setPoints([]);
      }
    })();
    return () => { cancelled = true; };
  }, [offer.source, offer.url]);

  if (!points || points.length < 2) return null;

  const first = points[0].price;
  const last = points[points.length - 1].price;
  const diffPct = ((last - first) / first) * 100;
  const stroke = diffPct < -2 ? "var(--color-good)"
    : diffPct > 2 ? "var(--color-bad)"
    : "var(--color-ink-4)";
  const label = diffPct < 0 ? `↓ ${Math.abs(diffPct).toFixed(1)}%` :
                diffPct > 0 ? `↑ ${diffPct.toFixed(1)}%` :
                "= стабильно";

  return (
    <div
      className="flex items-center gap-1.5 mt-1"
      title={`История цены: ${points.length} точек, изменение ${label}`}
    >
      <div className="w-16 h-5 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points}>
            <Line
              type="monotone"
              dataKey="price"
              stroke={stroke}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <span
        className="text-[10px] tabular-nums leading-none"
        style={{ color: stroke }}
      >
        {label}
      </span>
    </div>
  );
}
