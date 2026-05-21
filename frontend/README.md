# PricePulse — frontend

Next.js 16 + Tailwind v4 + React 19. Дизайн адаптирован из [MORENT — Pickolab Studio](https://www.figma.com/design/CXb8k66aF0IdblLbTr0lTx/Car-Rent-Website-Design---Pickolab-Studio--Community-?node-id=1-5) (Community).

## Запуск

```bash
cd frontend
pnpm install            # или npm install / yarn install
pnpm dev                # http://localhost:3000
```

Бэкенд должен быть запущен на `http://127.0.0.1:8000`:

```bash
cd ../backend
uv run uvicorn pricepulse.main:app --port 8000
```

## Что внутри

- `src/app/page.tsx` — главная (Hero × 2 + Топ-предложения + Рекомендации)
- `src/app/search/page.tsx` — выдача с фильтром по источникам
- `src/app/favorites/page.tsx` — избранное (требует auth)
- `src/app/login/page.tsx`, `src/app/register/page.tsx` — auth
- `src/components/ChatWidget.tsx` — плавающий чат с Gemma 4
- `src/components/ProductCard.tsx` — карточка товара (♥ в избранное)
- `src/lib/api.ts` — клиент `/api/v1/*` (proxy через Next rewrites)

## Дизайн tokens

См. `src/app/globals.css` — Tailwind v4 `@theme`. Палитра примерно соответствует фигме (primary `#3563E9`, slate-ink текст, белый фон).
