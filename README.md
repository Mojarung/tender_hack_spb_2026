# PricePulse — Tender Hack SPb 2026

> Интеллектуальный сервис поиска цен в открытых источниках.
> [ТЗ хакатона](./tz.md) · [Продукт-чеклист](./product.md) · [Backend](./backend/README.md) · [Frontend](./frontend/README.md)

PricePulse агрегирует цены товаров с Wildberries, Ozon, Яндекс Маркета и динамически выбираемого 4-го источника Рунета (Megamarket по умолчанию, Firecrawl/SearXNG для свободного поиска). Свободный режим без единого платежа — переключаемые feature-flags позволяют включить residential-прокси, 2Captcha и облачные scraper-API на проде.

## Что уже работает

### Backend (`backend/`)
- **Поиск** — `POST /api/v1/search` параллельно через 4 источника, fan-out + Best-Deal-ранжирование.
- **Streaming** — `GET /api/v1/search/stream` через SSE.
- **Price-history** — `GET /api/v1/price-history/{source}/{item_id}` (накопительно в Redis, sorted-set).
- **Sentiment** — `GET /api/v1/sentiment/{source}/{item_id}` через `seara/rubert-tiny2-russian-sentiment` (ленивая загрузка модели).
- **WB feedbacks** — fetcher `feedbacks{1,2}.wb.ru` с shard-fallback.
- **Auth** — `fastapi-users` JWT: `POST /auth/register`, `POST /auth/jwt/login`, `GET /users/me`.
- **Favorites** — `GET/POST/DELETE /api/v1/favorites` (требует JWT).
- **Chat-бот** — `POST /api/v1/chat`: Gemma 4 (Ollama) с tool-calling, история в Redis.
- **MCP-сервер** — `python -m pricepulse.agent.mcp_server http 8050` экспонирует те же tools для внешних агентов (Claude Code, Cursor).
- **Метрики** — `/metrics` (Prometheus instrumentator + кастомные `scrape_*` counters/histograms).
- **Admin landing** — `/admin` со ссылками на Grafana, n8n, pgAdmin, MinIO console, Ollama, и т.д.
- **Free-mode flag** — `FEATURES_ALLOW_PAID=false` killswitch, гранулярные `FEATURE_USE_*` флаги. `COST_CAP_USD=0` по умолчанию.

### Frontend (`frontend/`)
- **Next.js 16 + Tailwind v4** scaffold с дизайном по MORENT/Pickolab (primary `#3563E9`, белый surface).
- Страницы: `/`, `/search?q=...`, `/favorites`, `/login`, `/register`.
- Плавающий чат-виджет с Gemma 4.
- Карточка товара с «♥ в избранное» (с auth-fallback на `/login`).
- API-клиент через Next rewrites (same-origin).

### Инфраструктура (docker-compose)
- `api` + `worker` (arq) + `postgres` + `redis` + `minio`
- `prometheus` + `grafana` + `node-exporter` + `cadvisor` + `n8n`
- `firecrawl-api` + `searxng` (self-hosted 4-й источник)
- `ollama` (Gemma 4 локально)
- `ntfy` + `apprise` (уведомления)
- `dozzle` + `uptime-kuma` + `glitchtip` (observability)
- `pgadmin` + `homepage` (admin UIs)

## Стэк

| Слой | Что |
|---|---|
| Backend | Python 3.13, FastAPI 0.128, uv, SQLAlchemy 2.0 (async), fastapi-users, fastmcp |
| Scrapers (L1) | `httpx[http2]`, `curl_cffi` (TLS-impersonate Chrome 131) |
| Scrapers (L2) | Patchright + Camoufox (optional extra `stealth`) |
| L3 fallback | Firecrawl hosted (free 500 cr/mo) + Scrapfly/Apify/ZenRows free tiers (через feature-flag) |
| CAPTCHA | OpenCV slider solver + Gemma 4 vision (free) + 2Captcha (opt-in, RUB payments) |
| LLM локально | Ollama + Gemma 4 (`gemma4:e4b` ≈ 5 GB Q4) |
| NLP | `seara/rubert-tiny2-russian-sentiment` (optional extra `nlp`) |
| Frontend | Next.js 16, React 19, Tailwind v4, TanStack Query, Recharts, lucide-react |
| DB / Cache | Postgres 17 (prod) / SQLite (dev), Redis 7.4 |
| Observability | Prometheus + Grafana + Dozzle + Uptime Kuma + GlitchTip |

## Быстрый старт (local-dev)

```bash
# 1) Backend
cd backend
uv sync                       # base deps (no torch)
uv sync --extra nlp           # +sentiment (≈ 1.5 GB torch)
cp .env.example .env          # уже есть .env с разумными дефолтами
uv run uvicorn pricepulse.main:app --reload

# 2) Frontend
cd ../frontend
pnpm install
pnpm dev                      # http://localhost:3000

# 3) Локальный LLM-бот (опц.)
ollama serve &
ollama pull gemma4:e4b
```

## Документация

- [`backend/ARCHITECTURE.md`](./backend/ARCHITECTURE.md) — слои, контракты API, структура каталогов
- [`backend/docs/anti-bot.md`](./backend/docs/anti-bot.md) — стратегия защиты, L1→L5 cascade, per-source playbook
- [`backend/docs/free-mode.md`](./backend/docs/free-mode.md) — бесплатный стек по умолчанию, feature-flags
- [`backend/docs/local-llm-and-ops.md`](./backend/docs/local-llm-and-ops.md) — Gemma 4, OpenCV solver, ntfy, observability v2
- [`backend/docs/firecrawl-test-report.md`](./backend/docs/firecrawl-test-report.md) — почему Firecrawl ≠ silver bullet
- [`product.md`](./product.md) — продуктовые требования, DoD

## CHANGELOG

### 2026-05-21

- **Bot + MCP**: `POST /api/v1/chat` (Gemma 4 через Ollama, tool calling, история в Redis). Tools shared с MCP-сервером `pricepulse.agent.mcp_server` — экспортируется `search_products`, `get_top_deals`, `get_price_history`, `get_reviews_sample`, `compare_offers`.
- **Auth**: `fastapi-users` v15 (JWT, SQLAlchemy UUID-юзеры) + Favorites CRUD, dev-режим на SQLite, prod на Postgres.
- **Sentiment**: `seara/rubert-tiny2-russian-sentiment`, lazy-load, Redis-cache по hash текста, fallback на neutral без torch. Live-verified: 30 WB-отзывов → 92.3% positive / 3.8% / 3.8%.
- **WB feedbacks**: `feedbacks{1,2}.wb.ru/feedbacks/v1/{imt_id}` с shard-fallback, 1000 отзывов/запрос.
- **Best-Deal Score**: `top_deals[]` в `/api/v1/search` ответе, ранжирование по `price_z + rating + log(reviews)`.
- **Price-history**: Redis sorted-set, WB-адаптер пишет точку на каждый scrape, endpoint `/api/v1/price-history/...`.
- **MVP scrapers**: WB (search.wb.ru/v18 — реальные iPhone 15 128GB за 53 196 ₽), Ozon (mobile composer-api), YM (JSON-LD), Megamarket (mobile API), Runet (Firecrawl + JSON schema).
- **Orchestrator**: asyncio.gather, per-adapter exception isolation, Runet→Megamarket auto-fallback при пустой выдаче.
- **Frontend**: Next.js 16 + Tailwind v4 scaffold по дизайну MORENT (адаптация Car Rent → товары). Главная, /search, /favorites, /login, /register, плавающий чат.
- **Observability**: scraper-метрики `scrape_requests_total{source,outcome,proxy_tier}`, `scrape_duration_seconds`, `scrape_offers_returned_total` — все 4 адаптера инструментированы.
- **Free-mode**: killswitch `FEATURES_ALLOW_PAID=false` + гранулярные флаги. Cost-cap = $0 по дефолту.
- **Локальный LLM-стэк**: docker-compose `ollama` сервис, инструкция `ollama pull gemma4:e4b`.
- **MCP** для Claude Code: `.mcp.json.example` с Firecrawl MCP + наш собственный `pricepulse.agent.mcp_server`.
- **Admin landing**: `/admin` HTML + Homepage `:3030` со ссылками на все сервисы.

### Что в работе / next

- Real-IP проверка Ozon/YM на сервере с residential RU IP.
- Patchright/Camoufox L2 wiring (опциональная extra `stealth`).
- arq periodic worker для backfill price-history.
- Product detail page (`/product/[source]/[id]`) с sparkline + reviews + similar.
- Sentiment + price-history embedded в карточки на главной.

## Лицензия

MIT.
