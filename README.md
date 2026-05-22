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
| Scrapers (L2) | nodriver — CDP-direct стелс-браузер, без WebDriver (optional extra `stealth`) |
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

### 2026-05-23

- **JamSpell микросервис** для общеязыковой RU-коррекции опечаток:
  - `backend/jamspell/` — Docker-образ с C++ движком JamSpell и моделью
    из `bakwc/JamSpell-models` (`ru.tar.gz`). FastAPI-обёртка `/fix` +
    `/health`. Self-hosted, никаких внешних API.
  - `enrichment/jamspell_client.py` — async HTTP-клиент с graceful
    degradation (сервис лёг → `None`, `normalize_query` идёт без коррекции).
  - `enrichment/normalize.py` переписан: brand-RapidFuzz → **JamSpell** →
    translit → синонимы. `NormalizedQuery` обогащён аудит-нотами
    «опечатка: ...».
  - `JAMSPELL_URL` в `config.py` / `.env.example` (по умолчанию `""` =
    выключен; в docker — `http://jamspell:8080`).
  - `docker-compose.yml` — новый сервис `jamspell` на host-порту `8095`,
    healthcheck по `/health`.
  - Тесты: `test_jamspell.py` (+10 кейсов через `respx`-мок: enabled/disabled,
    HTTP 5xx, ConnectError, интеграция в `normalize_query`). 51 passed.

- **Methodology compliance** (`final_presa.pdf` p.5 — «полный запрет на любые внешние API»):
  - Удалён `antibot/captcha.py` (2captcha — внешний API).
  - Удалён `scrapers/megamarket.py` (Megamarket — маркетплейс, запрещён как 4-й источник).
  - `scrapers/runet.py` переписан: SearXNG self-hosted → топ не-маркетплейс URL →
    `curl_cffi` GET → JSON-LD `Product` парсер. Без Firecrawl-cloud, без Gemini/DeepSeek/Scrapfly/Apify/ZenRows.
  - `antibot/cascade.py` ужат до L1→L3 (HTTP-impersonate → стелс-браузер → локальная капча).
    L3-third-party и L5-paid-captcha удалены, `FeatureFlags` свёрнут до `demo_mode`.
  - `config.py` / `.env.example` — удалены `TWOCAPTCHA_API_KEY`, `SCRAPFLY_API_KEY`,
    `APIFY_API_TOKEN`, `ZENROWS_API_KEY`, `FIRECRAWL_API_KEY`, `FIRECRAWL_URL`,
    `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, все `FEATURE_USE_*`, `FEATURES_ALLOW_PAID`,
    `COST_CAP_USD`.
  - `OLLAMA_URL` теперь явно поддерживает два варианта: удалённый Ollama на cloud-VM
    (dev/staging, всё ещё своя инфра) и локальный (`http://ollama:11434`, docker).
  - `backend/searxng/settings.yml` + volume-mount в `docker-compose.yml` — включает
    `format=json` для SearXNG; Яндекс-engine отключён.
  - Удалён `frontend2/` (Vite-прототип не собирался: отсутствует `src/lib/`).
  - Тесты: `test_cascade.py` обновлён под новый Layer enum, `test_orchestrator.py` —
    тест Megamarket-fallback заменён на тест «пустой Runet остаётся пустым». 41 passed.

### 2026-05-22

- **Anti-bot слой переделан** под актуальное состояние инструментов (см. [`CLAUDE.md`](./CLAUDE.md)):
  - `antibot/ratelimit.py` — реальный token-bucket на Redis (атомарный Lua-скрипт), с graceful-degradation в process-local bucket при недоступности Redis. Вшит в оркестратор — каждый запрос к источнику ждёт токен (`wb_rpm`/`ozon_rpm`/…).
  - `antibot/browser_pool.py` — L2 стелс-браузер на **nodriver** (CDP-direct, без WebDriver — обходит automation-protocol fingerprinting). Camoufox отвергнут (beta, год без поддержки), Patchright — по бенчмарку May 2026 уступает nodriver.
  - `antibot/cascade.py` вшит в оркестратор — circuit-breaker эскалирует слой L1→L4 (L5 за платным флагом) после 3 блокировок источника в окне 60 с.
  - `antibot/browser_fetch.py` — L2-путь Ozon: прогрев сессии в браузере → OpenCV-солвер slider-капчи → fetch composer-api тем же origin (переиспользует L1-парсеры).
  - `antibot/captcha.py` — реальная интеграция 2captcha для Yandex SmartCaptcha (только за платным флагом).
  - Тесты: `test_ratelimit.py` + `test_cascade.py` (14 тестов). Весь сьют — 35 passed.
  - Починен пред-существующий фейл `test_search_empty_groups` (повторная регистрация Prometheus-метрик при `create_app()` в фикстуре).

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
