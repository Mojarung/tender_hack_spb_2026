export function formatPrice(p: string | number, currency = "RUB"): string {
  const n = typeof p === "number" ? p : Number(p);
  if (!Number.isFinite(n)) return String(p);
  const v = n.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  return currency === "RUB" ? `${v} ₽` : `${v} ${currency}`;
}

export function itemIdFromOffer(o: { source: string; url: string }): string {
  if (o.source === "wb") {
    const m = o.url.match(/catalog\/(\d+)/);
    return m?.[1] ?? o.url;
  }
  return o.url;
}
