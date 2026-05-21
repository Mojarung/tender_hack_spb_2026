# PricePulse — backend

Backend для сервиса агрегированного поиска цен. Полная архитектура — в [ARCHITECTURE.md](./ARCHITECTURE.md), стратегия защиты — в [docs/anti-bot.md](./docs/anti-bot.md), бесплатный стек — в [docs/free-mode.md](./docs/free-mode.md), требования — в [../product.md](../product.md), оригинал ТЗ — в [../tz.md](../tz.md).

## Что уже работает (MVP, 21.05.2026)

- [x] **Wildberries** — `search.wb.ru/v18`, цены в копейках, basket-CDN картинки, retry на 429
- [x] **Ozon** — `composer-api.bx` mobile (требует residential RU IP для production)
- [x] **Yandex Market** — JSON-LD parser, fallback на Camoufox при SmartCaptcha
- [x] **Megamarket** — mobile API с warm-up cookies (используется как fallback для 4-го источника)
- [x] **Runet (4-й источник)** — Firecrawl `/v2/search` + `/v2/scrape` с JSON schema, hosts-фильтр
- [x] **SearchOrchestrator** — fan-out (asyncio.gather), exception-isolation, кэш-aware
- [x] **SSE streaming** — `/api/v1/search/stream`
- [x] **Free-mode killswitch** — `FEATURES_ALLOW_PAID=false` по умолчанию
- [x] **Prometheus instrumentation** — `/metrics` с bounded-cardinality лейблами
- [x] **Admin landing** — `/admin` (FastAPI) + Homepage `:3030`

## Быстрый старт

### Локально

```bash
# Python 3.13 + uv установлены (https://docs.astral.sh/uv/)
uv sync                    # установит deps в .venv
cp .env.example .env       # отредактируй прокси/ключи при необходимости
uv run uvicorn pricepulse.main:app --reload
# OpenAPI: http://localhost:8000/docs
```

### В Docker

```bash
cp .env.example .env
docker compose up --build
# api:        http://localhost:8000/docs
# firecrawl:  http://localhost:3002
# searxng:    http://localhost:8080
```

## Полезные команды

```bash
uv run ruff check .            # линт
uv run mypy src                # типы
uv run pytest                  # unit-тесты (6 проходят зелёным)
uv run pytest -m live          # live-тесты, бьющие в реальные источники
```

### Запустить вживую и проверить (без docker-compose)

```bash
uv sync                                 # установит deps в .venv
uv run uvicorn pricepulse.main:app --port 8000

# В соседнем окне:
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"iphone 15","max_per_source":3}'

# Прямой smoke-скрипт (всю цепочку через orchestrator):
uv run python scripts/smoke_search.py "iphone 15" 3
```

Ожидаемый результат на чистой Windows/Mac/Linux машине без прокси:

- **WB** — 3 живых offer'а с реальными ценами и URL
- **Ozon** — HTTP 403 (нужен residential RU IP, выключен в free-mode)
- **Yandex Market** — HTTP 403 (нужен Camoufox + warm cookies)
- **Runet** — 1–3 offer'а через Firecrawl (если задан `FIRECRAWL_API_KEY`)

## Структура

См. [ARCHITECTURE.md, раздел 4](./ARCHITECTURE.md#4-структура-каталогов).
