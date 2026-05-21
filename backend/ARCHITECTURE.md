# PricePulse — Архитектура backend

Версия 0.1, 2026-05-21. Источники требований: [tz.md](../tz.md), [product.md](../product.md).

---

## 1. Принципы

1. **Async-first**. Любой I/O (HTTP, БД, Redis, браузер) — через asyncio.
2. **Источники изолированы**. Падение/блокировка одного адаптера не валит остальные. Каждый адаптер реализует `ScraperProtocol` и живёт в собственном модуле.
3. **Стриминг наружу**. Клиент получает результаты по мере готовности (SSE/WebSocket), а не ждёт самого медленного.
4. **Кэш — первичный citizen**. Любой запрос к внешнему источнику кэшируется (Redis, TTL 1–6 ч). Это и UX, и обход rate-limit.
5. **Anti-bot не в адаптере**. Логика стелс-браузера, прокси, капчи — общий слой `antibot/`. Адаптер просто говорит «дай мне страницу X» или «дай мне HTTP-сессию с прокси».
6. **Никаких платных Seller API**. Они не дают данные по чужим селлерам и привязаны к токену магазина. Идём через публичные витринные эндпоинты + браузер для тяжёлых случаев.

---

## 2. Высокоуровневая схема (логическая)

```
                ┌────────────────┐
                │   Frontend     │
                │  (Next.js TBD) │
                └────────┬───────┘
                         │ HTTPS + SSE
                ┌────────▼───────────────────────────┐
                │ FastAPI / Uvicorn                  │
                │  ┌──────────────────────────────┐  │
                │  │ /search   /stream   /health  │  │
                │  └──────────────────────────────┘  │
                │  ┌──────────────────────────────┐  │
                │  │ SearchOrchestrator           │  │
                │  │  • normalize query           │  │
                │  │  • fan-out → 4 adapters      │  │
                │  │  • merge → SSE stream        │  │
                │  └────────────┬─────────────────┘  │
                └───────────────┼────────────────────┘
                                │
                ┌───────────────┴───────────────┐
       ┌────────▼────────┐             ┌────────▼────────┐
       │  Query enrich   │             │  Result store   │
       │  • typo (sym-   │             │  Postgres       │
       │    spell/RF)    │             │  • products     │
       │  • synonyms     │             │  • queries      │
       │  • lemmas       │             │  • offers       │
       └─────────────────┘             └─────────────────┘

  Adapters (parallel, asyncio.gather):
  ┌────────┐  ┌────────┐  ┌─────────────┐  ┌──────────┐
  │  WB    │  │  Ozon  │  │ Y.Market    │  │  Runet   │
  │ httpx  │  │Patchr- │  │ Camoufox +  │  │ Firecrawl│
  │ public │  │ight    │  │ captcha     │  │ + SearXNG│
  └───┬────┘  └───┬────┘  └──────┬──────┘  └─────┬────┘
      └──────┬────┴──────┬───────┴──────────┬────┘
             │           │                  │
        ┌────▼─────┐ ┌───▼─────────┐ ┌──────▼────────┐
        │ Browser  │ │ Proxy pool  │ │ CAPTCHA solver│
        │ pool     │ │ (resi+DC)   │ │ (2captcha/    │
        │ (Patch+  │ │ rotation    │ │  CapMonster)  │
        │  Camou)  │ └─────────────┘ └───────────────┘
        └──────────┘
                              ▲
                              │
                       ┌──────┴──────┐
                       │   Redis     │
                       │ • cache     │
                       │ • arq queue │
                       │ • ratelimit │
                       └─────────────┘
```

---

## 3. Стек (по состоянию на 2026-05-21)

| Слой | Технология | Почему |
|---|---|---|
| Менеджер пакетов | **uv** 0.5+ | 10–100× быстрее pip, единый lock, `uv run` |
| Python | **3.13** | freethreaded GIL-less для CPU-bound нормализации |
| API | **FastAPI** 0.128+ | async, OpenAPI из коробки, `lifespan` |
| ASGI server | **uvicorn** 0.34+ (uvloop) | стандарт |
| Валидация / Settings | **pydantic** v2, **pydantic-settings** | типобезопасный конфиг из ENV |
| HTTP клиент | **httpx[http2]** 0.28+ | async, HTTP/2 для WB/Ozon public endpoints |
| Браузер-стелс (Chromium) | **Patchright** 1.50+ | CDP-patches, обходит DataDome лучше всего на 2026 |
| Браузер-стелс (Firefox) | **Camoufox** 0.4+ | C++-уровень спуфинга, статистически точный fingerprint rotation |
| CAPTCHA | **2captcha** SDK или CapMonster Cloud | для Yandex SmartCaptcha (есть free trial) |
| Кэш / очередь | **Redis** 7.4 + **arq** 0.26+ | async-friendly очередь задач |
| БД | **PostgreSQL** 17 + **asyncpg** + **SQLAlchemy** 2 async | проверенный стек |
| Миграции | **alembic** | стандарт |
| Морфология RU | **pymorphy3** | лемматизация русского |
| Fuzzy / опечатки | **rapidfuzz** + **symspellpy** | C++-скоростные |
| Логирование | **structlog** + JSON | structured logs для дебага многопоточных скрейпов |
| Стриминг | **sse-starlette** | SSE для отдачи результатов клиенту |
| Тесты | **pytest** + **pytest-asyncio** + **respx** | mock httpx, async-aware |
| Линт/тайпчек | **ruff** + **mypy --strict** | |
| Контейнеризация | Docker, docker compose | стандарт демо |
| Поиск (4-й источник) | **Firecrawl** (self-host) + **SearXNG** | бесплатный мета-поиск Рунета + LLM-ready markdown |

---

## 4. Структура каталогов

```
backend/
├── pyproject.toml                 # uv project (см. ниже)
├── uv.lock                        # генерится через `uv lock`
├── Dockerfile                     # multi-stage build, основан на astral-sh/uv-docker-example
├── docker-compose.yml             # api + worker + postgres + redis + firecrawl + playwright
├── .env.example
├── .dockerignore
├── .gitignore
├── README.md                      # как запустить
├── ARCHITECTURE.md                # этот файл
├── alembic.ini                    # пустая до первой миграции
├── docs/
│   └── bpmn.placeholder.md        # сюда положим bpmn.svg перед защитой
├── src/
│   └── pricepulse/
│       ├── __init__.py
│       ├── main.py                # FastAPI entrypoint + lifespan
│       ├── config.py              # pydantic-settings (12-factor)
│       ├── core/
│       │   ├── logging.py
│       │   └── exceptions.py
│       ├── domain/
│       │   ├── models.py          # ProductOffer, SourceGroup, SearchResult (pydantic)
│       │   └── enums.py           # SourceKind
│       ├── api/
│       │   ├── deps.py            # DI: settings, redis, db
│       │   └── routes/
│       │       ├── search.py      # POST /search (sync mode)
│       │       ├── stream.py      # GET  /search/stream  (SSE)
│       │       └── health.py
│       ├── orchestrator/
│       │   └── search.py          # fan-out, merge, stream
│       ├── enrichment/
│       │   ├── normalize.py       # lemmatize + lowercase + clean
│       │   ├── typos.py           # symspell wrapper
│       │   └── synonyms.py        # хардкод словарик + расширения
│       ├── scrapers/
│       │   ├── base.py            # ScraperProtocol, ScrapeResult
│       │   ├── wb.py              # search.wb.ru
│       │   ├── ozon.py            # composer-api.bx + Patchright fallback
│       │   ├── yandex_market.py   # Camoufox + SmartCaptcha
│       │   └── runet.py           # Firecrawl /search + scrape
│       ├── antibot/
│       │   ├── browser_pool.py    # Patchright + Camoufox singletons
│       │   ├── proxy_pool.py      # ротация
│       │   ├── fingerprints.py    # генерация UA + viewport + lang
│       │   ├── captcha.py         # 2captcha client
│       │   └── ratelimit.py       # token bucket per host
│       ├── cache/
│       │   └── redis_cache.py     # get/set с TTL и неймспейсами
│       ├── queue/
│       │   └── tasks.py           # arq worker definitions
│       └── storage/
│           ├── db.py              # async engine, session
│           ├── models.py          # SQLAlchemy mapped classes
│           └── repositories/
│               ├── products.py
│               └── queries.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── unit/
    │   ├── test_normalize.py
    │   └── test_orchestrator.py
    └── live/
        └── test_scrapers_smoke.py  # @pytest.mark.live, не в CI по дефолту
```

---

## 5. Контракты API

### 5.1 POST `/api/v1/search` — синхронный поиск

```jsonc
// Request
{
  "query": "айфон 15 128 гб",
  "max_per_source": 10,
  "sources": ["wb", "ozon", "ya_market", "runet"] // опционально
}

// Response 200
{
  "query": {
    "raw": "айфон 15 128 гб",
    "normalized": "iphone 15 128 gb",
    "expansions": ["apple iphone 15", "smartphone apple"]
  },
  "groups": [
    {
      "source": "wb",
      "count": 12,
      "min_price": 79990,
      "currency": "RUB",
      "offers": [/* ProductOffer[] */]
    }
    // ...
  ],
  "took_ms": 4521,
  "partial": false
}
```

### 5.2 GET `/api/v1/search/stream?query=...` — SSE-стриминг

Server-Sent Events. Каждое событие — JSON. Типы:
- `event: source_started` — `{"source": "ozon"}`
- `event: offer` — `{"source": "ozon", "offer": ProductOffer}`
- `event: source_finished` — `{"source": "ozon", "count": 8, "min_price": 79990}`
- `event: error` — `{"source": "ya_market", "code": "captcha", "message": "..."}`
- `event: done` — `{"took_ms": 4521}`

Это закрывает требование «удобный UI без зависшего спиннера».

### 5.3 GET `/health` — healthcheck

`200 {"status": "ok", "checks": {"redis": "ok", "db": "ok"}}`

---

## 6. ScraperProtocol

```python
class ScraperProtocol(Protocol):
    source: SourceKind

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: Callable[[ProductOffer], Awaitable[None]] | None = None,
    ) -> ScrapeResult: ...
```

`on_offer` колбэк — для стриминга. Если он передан — адаптер вызывает его на каждое предложение по мере парсинга; финальный `ScrapeResult` всё равно возвращается (для статистики/кэша).

---

## 7. Стратегии по источникам

### 7.1 Wildberries (lightweight)
- Эндпоинт: `https://search.wb.ru/exactmatch/ru/common/v9/search` (публичный, отдаёт JSON).
- Параметры: `query`, `appType=1`, `curr=rub`, `dest=...`, `resultset=catalog`, `sort=popular`.
- Доп. эндпоинт цен: `https://card.wb.ru/cards/v2/detail` по `nm` ID.
- **Стратегия**: чистый `httpx` async, ротация UA, `dest` подмешивается случайный из набора регионов, retry с backoff, кэш 1ч.

### 7.2 Ozon (medium)
- Публичный витринный API: `https://api.ozon.ru/composer-api.bx/page/json/v2?url=/category/...`.
- DataDome агрессивно фильтрует. Сначала пробуем httpx с правильными заголовками + резидентный прокси.
- При получении JS-челленджа / 403 — fallback на **Patchright** headless с пулом fingerprint'ов.
- Кэш 6 ч (Ozon реже обновляет цены, чем WB).

### 7.3 Yandex Market (hard)
- Поиск: `https://market.yandex.ru/search?text=...`.
- Защита: **Yandex SmartCaptcha**, статистически частая.
- Стратегия:
  1. **Camoufox** (Firefox stealth) + резидентный прокси из RU/CIS пула.
  2. На детект капчи: page.locator('iframe[src*="smartcaptcha"]') → выдрать sitekey → 2captcha → ввести токен → продолжить.
  3. Парсинг через DOM (selectors хранятся в `scrapers/yandex_market_selectors.py` для лёгкого обновления — Маркет меняет вёрстку ~раз в 2 мес).
  4. Кэш 6 ч. Если за день получили >10 капч с одного прокси — banned, ротация.

### 7.4 Неформализованные источники Рунета
- Через **Firecrawl** self-hosted, который умеет `/v2/search`.
- В `.env` Firecrawl: `SEARXNG_ENDPOINT=http://searxng:8080` — поиск идёт через локальный SearXNG (бесплатно, без Google API).
- Топ-N результатов (исключая уже spider'ы трёх маркетплейсов выше) → Firecrawl `/v2/scrape` с `formats: ["json"]` и кастомным extraction schema:
  ```json
  { "name": "string", "price": "number", "currency": "string",
    "image": "string", "url": "string", "characteristics": "object" }
  ```
- LLM-extraction опционально через Ollama (`MODEL_NAME=deepseek-r1:7b`) — для непривычных карточек.
- **4-й источник плавающий** — это и решает требование «не может быть фиксированным».

---

## 8. Anti-bot слой

### 8.1 Browser pool
- Один pool на источник (изоляция fingerprint'ов).
- Patchright для Ozon, Camoufox для Yandex Market.
- Контексты переиспользуются (warm), но fingerprint пересеивается каждые N запросов.
- Pool size конфигурируется (`OZON_BROWSER_POOL=2` в `.env`).

### 8.2 Proxy pool
- Конфиг через `.env`:
  ```
  PROXY_POOL_RESI=user:pass@host1:port,user:pass@host2:port
  PROXY_POOL_DC=...
  ```
- Стратегия: residential для Yandex/Ozon, datacenter для WB/Runet.
- Привязка sticky session: одна и та же связка `(UA, proxy, viewport)` живёт 5–15 минут.

### 8.3 CAPTCHA
- 2captcha-python (`twocaptcha-python`) — Yandex SmartCaptcha поддерживается.
- API ключ в `.env`, мягкий graceful degrade если ключа нет (источник вернёт пустой результат + лог).

### 8.4 Rate-limit
- Token-bucket в Redis по ключу `ratelimit:{source}:{proxy}` — N запросов в минуту.
- На превышении — задача переходит в очередь arq с delay, не блокирует основной поток.

---

## 9. Кэш-стратегия

| Ключ | TTL | Инвалидируется |
|---|---|---|
| `cache:wb:{normalized_query}` | 1h | по `Cache-Bust` от админки |
| `cache:ozon:{normalized_query}` | 6h | то же |
| `cache:ya_market:{normalized_query}` | 6h | то же |
| `cache:runet:{normalized_query}` | 12h | то же |
| `enrich:typos:{raw}` | 24h | — |

При попадании по кэшу — отдаём в SSE как `event: offer` с флагом `cached: true`, метрику пишем отдельно.

---

## 10. Очередь и масштабирование

- **arq** worker контейнер запускается отдельно (см. `docker-compose.yml`).
- Тяжёлые задачи (Patchright/Camoufox scrape) уходят в очередь, результат — в Redis, основной API получает через subscribe.
- Горизонтальное масштабирование: `docker compose up --scale worker=4`.
- В синхронном API-режиме (`/search` без stream) — тоже через очередь, но `await` результата с таймаутом.

---

## 11. Persistance (Postgres)

Минимально:
- `products` — нормализованная карточка после merge между источниками (по хэшу/embedding'у).
- `offers` — конкретное предложение (источник, цена, ссылка, ts).
- `queries` — журнал запросов (для аналитики и обучения тезауруса синонимов).
- `synonyms` — пользовательский тезаурус (можно расширять без релиза).

Для хакатона: запускаем с пустой БД, миграции через alembic. Если не успеваем — Postgres опционален, состояние держим только в Redis + memory.

---

## 12. Что вне scope этой версии

- Аутентификация / пользователи.
- Запись в zakupki.mos.ru.
- Мобильное приложение.
- ML-ранжирование между источниками (стартуем с эвристик: совпадение токенов + цена в пределах ±30% от медианы).

---

## 13. Запуск

```bash
# Локально (dev)
cd backend
uv sync
uv run uvicorn pricepulse.main:app --reload

# Через docker
docker compose up --build
# API:        http://localhost:8000/docs
# Firecrawl:  http://localhost:3002
# SearXNG:    http://localhost:8080
```

---

## 14. Источники, на которых построены решения

- uv project layout — [astral-sh/uv docs](https://docs.astral.sh/uv/)
- FastAPI lifespan / SSE — [fastapi.tiangolo.com](https://fastapi.tiangolo.com/)
- Firecrawl self-host — [github.com/firecrawl/firecrawl SELF_HOST.md](https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md)
- Patchright (CDP-stealth Chromium) — выбран как наиболее устойчивый против DataDome (anti-detect tools comparison, май 2026).
- Camoufox (Firefox C++ stealth) — выбран против Yandex SmartCaptcha (статистически корректная ротация fingerprint'ов).
- 2captcha — единственный массовый солвер с поддержкой Yandex SmartCaptcha.
