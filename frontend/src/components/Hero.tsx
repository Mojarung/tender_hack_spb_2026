import Link from "next/link";

/** MORENT-style dual-hero adapted for PricePulse — two banners side-by-side. */
export function Hero() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <Banner
        title="Лучший агрегатор цен на маркетплейсах"
        sub="Сравните Wildberries, Ozon, Яндекс Маркет и сотни магазинов Рунета в одном поиске — без бана и подписок."
        cta="Начать поиск"
        href="/search"
        tone="primary"
      />
      <Banner
        title="Локальный AI-ассистент"
        sub="Спросите Gemma 4 — найдём товар, объясним разницу в цене, покажем тренд и отзывы."
        cta="Открыть чат"
        href="/?chat=1"
        tone="secondary"
      />
    </div>
  );
}

function Banner({
  title,
  sub,
  cta,
  href,
  tone,
}: {
  title: string;
  sub: string;
  cta: string;
  href: string;
  tone: "primary" | "secondary";
}) {
  return (
    <Link
      href={href}
      className={
        tone === "primary"
          ? "relative overflow-hidden rounded-[10px] bg-[var(--color-brand-500)] p-8 text-white block group"
          : "relative overflow-hidden rounded-[10px] bg-gradient-to-br from-[var(--color-brand-700)] to-[var(--color-ink-900)] p-8 text-white block group"
      }
    >
      <div className="max-w-[60%]">
        <h2 className="text-2xl font-semibold leading-snug">{title}</h2>
        <p className="mt-3 text-sm/relaxed text-white/85">{sub}</p>
        <span className="inline-flex items-center gap-2 mt-6 bg-white/15 hover:bg-white/25 transition-colors rounded-[4px] px-4 py-2 text-sm font-semibold backdrop-blur">
          {cta}
        </span>
      </div>
      {/* Decorative angled stripes — picks up the MORENT vibe without a hero photo */}
      <div className="absolute -right-12 -bottom-10 w-80 h-80 rounded-full bg-white/5 blur-2xl group-hover:bg-white/10 transition-colors" />
      <div className="absolute right-10 bottom-6 w-32 h-32 rounded-full bg-white/10 blur-xl" />
    </Link>
  );
}
