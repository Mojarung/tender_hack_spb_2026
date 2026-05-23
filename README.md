# PricePulse

> Интеллектуальный агрегатор цен для **Портала поставщиков** zakupki.mos.ru.
> Хакатон **Tender Hack SPb 2026**, защита **24.05.2026**.

PricePulse за один запрос показывает поставщику минимальную и среднюю цену
товара на **Wildberries**, **Ozon**, **Яндекс Маркете** и в **открытом Рунете**
(self-hosted SearXNG → JSON-LD), с поправкой на регион, опечатки и синонимы.
Весь стек **on-prem** — методичка `final_presa.pdf` p.5 запрещает любые внешние
платные API (никаких 2Captcha, Firecrawl-cloud, Gemini, Scrapfly и т.п.).

📚 **Источник истины по архитектуре** — [`CLAUDE.md`](./CLAUDE.md).
📋 **ТЗ хакатона** — [`tz.md`](./tz.md) · **продуктовые требования** — [`product.md`](./product.md).

---

## 1. Что внутри

### Backend (`backend/`, Python 3.13 + FastAPI, всё async)

| Эндпоинт | Назначение |
|---|---|
| `POST /api/v1/search` | Параллельный fan-out по 4 источникам, группировка, Best-Deal-ранжирование |
| `GET  /api/v1/search/stream` | То же, но через **SSE** — карточки прилетают по мере готовности |
| `GET  /api/v1/price-history/{source}/{item_id}` | История цен в Redis sorted-set |
| `GET  /api/v1/sentiment/{source}/{item_id}` | Тональность отзывов через `rubert-tiny2` (optional extra `nlp`) |
| `POST /api/v1/chat` | Локальный чат-бот: Gemma 4 (Ollama) + tool-calling |
| `GET  /metrics` | Prometheus с bounded-cardinality лейблами |
| `GET  /admin` | Дашборд со ссылками на Grafana / pgAdmin / MinIO / etc. |

**Поиск-пайплайн** (`orchestrator/search.py`):
`normalize_query` → fan-out `asyncio.gather` → группировка `SourceGroup` (min / avg / median) → ранжирование.

**Нормализация запроса** (`enrichment/normalize.py`):
clean → бренд-fuzzy (RapidFuzz) → **SAGE FRED-T5** (микросервис) → транслит RU↔EN → синонимы (pymorphy3 + курируемый тезаурус). Весь результат кэшируется в Redis по `sha1(raw)` — повтор <2 мс (cold ≈ 900 мс).

**Anti-bot каскад** (`antibot/`, L0 → L3, всё on-prem):

| Слой | Что | Когда |
|---|---|---|
| **L0** | Redis token-bucket (атомарный Lua) | Всегда |
| **L1** | `curl_cffi` (TLS-impersonate Chrome 131) | По умолчанию (WB/YM) |
| **L2** | `nodriver` (CDP-direct стелс-браузер) | Эскалация по circuit-breaker (Ozon) |
| **L3** | OpenCV slider + Gemma 4 vision via Ollama | На капчу |

### Frontend (`frontend/`, Next.js 16 + Tailwind v4)
Главная, `/search`, `/favorites`, `/login`, `/register`, плавающий чат-виджет, карточка товара. Селектор региона из 89 канонических Yandex `lr`-кодов.

### Микросервисы (отдельные docker-сервисы)

| Сервис | Назначение | Порт (host) |
|---|---|---|
| `spellcheck` | SAGE FRED-T5 distilled-95M (Сбер, MIT, F1=78.9 на RUSpellRU) | 8095 |
| `searxng` | URL-discovery для 4-го источника (Рунет) | 8080 |
| `ollama` | Gemma 4 для VLM-капчи и chat. **Опционален** (`--profile gpu`) — в проде живёт на отдельной GPU-машине | 11434 |
| `redis` / `postgres` / `minio` | Storage / cache / image cache | стандартные |
| `prometheus` / `grafana` / `dozzle` / `uptime-kuma` / `glitchtip` | Observability | стандартные |

---

## 2. Быстрый старт

### Backend
```bash
cd backend
uv sync                                              # базовые зависимости
uv sync --extra stealth                              # +nodriver (L2 браузер)
cp .env.example .env
uv run uvicorn pricepulse.main:app --reload          # http://localhost:8000/docs
```

### Микросервисы (минимум для полного пайплайна)
```bash
cd backend
docker compose up -d redis postgres searxng spellcheck
# Ollama — отдельным шагом, см. §3.
```

### Frontend
```bash
cd frontend
npm install
npm run dev                                          # http://localhost:3000
```

### Тесты и линт
```bash
cd backend
uv run pytest -q                                     # 62 passed (на 2026-05-23)
uv run ruff check src/ tests/                        # clean
```

---

## 3. Ollama как отдельный микросервис (prod-friendly)

VLM-инференс на CPU — медленный. В проде Ollama поднимается на **отдельной
машине с GPU**, backend ходит к нему по HTTP. Настройка — одной переменной:

```bash
# .env
OLLAMA_URL=http://ollama-gpu.internal.example.com:11434
OLLAMA_VISION_MODEL=gemma4:e4b
```

Для локальной отладки compose-сервис `ollama` остался, но **opt-in**:
```bash
docker compose --profile gpu up -d ollama
ollama pull gemma4:e4b
```

Если `OLLAMA_URL` недоступен — slider-solver продолжает работать, текстовый
пайплайн поиска полностью функционален (graceful degradation).

---

## 4. Конвенции (важно при работе над проектом)

- **Python 3.13, uv везде** — в локалке, в Dockerfile (`ghcr.io/astral-sh/uv:python3.11-bookworm-slim`), в CI. Никакого `pip install`.
- **Никаких внешних API в проде** — методичка p.5. Сторонние сервисы (SAGE, SearXNG, Ollama) поднимаем только локально или на своих серверах.
- **Прежде чем добавлять зависимость** — web-research: актуальная ли, MIT-совместима ли, не противоречит ли методичке.
- **Async everywhere**. Pydantic v2, structlog.
- **ruff** `line-length = 120`. Полные правила — `backend/pyproject.toml`.
- **Коммиты — без Co-Authored-By: Claude**.

---

## 5. Структура репозитория

```
backend/                  # FastAPI, источник истины по логике
  src/pricepulse/
    api/                  # routes, cache/limiter singletons
    orchestrator/         # SearchOrchestrator
    scrapers/             # wb, ozon, yandex_market, runet
    enrichment/           # normalize, spellcheck client, thesaurus
    antibot/              # ratelimit, browser_pool, cascade, vlm_solver
    analytics/            # scoring, sentiment
  spellcheck/             # SAGE FRED-T5 микросервис (отдельный Dockerfile)
  docker-compose.yml
  pyproject.toml
frontend/                 # Next.js 16 (единственный фронт)
.github/workflows/ci.yml  # ruff + pytest + frontend typecheck
CLAUDE.md                 # архитектура / конвенции / открытые дыры
tz.md, product.md         # требования
final_presa.pdf           # методичка организаторов
```

---

## 6. Лицензия

MIT — за исключением **L2-браузера**: `nodriver` распространяется под **AGPL-3.0**,
поэтому изолирован в опциональной extra `stealth` + ленивый импорт.
