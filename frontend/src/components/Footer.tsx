export function Footer() {
  return (
    <footer className="bg-white mt-16 border-t border-[var(--color-ink-100)]">
      <div className="max-w-[1240px] mx-auto px-6 py-12 grid grid-cols-1 md:grid-cols-4 gap-8 text-sm">
        <div className="md:col-span-1">
          <div className="text-2xl font-bold text-[var(--color-brand-500)]">PricePulse</div>
          <p className="mt-3 text-[var(--color-ink-500)] leading-relaxed">
            Агрегатор цен с маркетплейсов и магазинов Рунета. Парсим WB, Ozon, Я.Маркет и не только.
          </p>
        </div>
        <FooterCol title="Сервис" items={["Поиск", "Избранное", "Аналитика цен", "API"]} />
        <FooterCol title="Сообщество" items={["GitHub", "Tender Hack СПб 2026", "Блог", "Контакты"]} />
        <FooterCol title="Юридическое" items={["Privacy", "Terms", "Cookies", "О проекте"]} />
      </div>
      <div className="border-t border-[var(--color-ink-100)] py-4">
        <div className="max-w-[1240px] mx-auto px-6 text-xs text-[var(--color-ink-400)] flex justify-between">
          <span>© 2026 PricePulse · Сделано на Tender Hack SPB 2026</span>
          <span>Гарантируем что ничего не покупаем — только показываем цены</span>
        </div>
      </div>
    </footer>
  );
}

function FooterCol({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="font-semibold text-[var(--color-ink-900)]">{title}</div>
      <ul className="mt-3 space-y-2 text-[var(--color-ink-500)]">
        {items.map((it) => <li key={it}>{it}</li>)}
      </ul>
    </div>
  );
}
