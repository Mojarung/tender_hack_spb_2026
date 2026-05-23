# CLAUDE.md — PricePulse

Контекст для AI-агента: чтобы при переезде на другой компьютер не терять понимание
проекта и принятых решений. Источник истины по требованиям — `tz.md` + методичка
`final_presa.pdf` + `product.md`.

## Что это

PricePulse — интеллектуальный агрегатор цен для **Портала поставщиков**
(zakupki.mos.ru). Проект хакатона **Tender Hack SPb 2026**, защита **24.05.2026**.
Задача: собрать цены товара с Wildberries, Ozon, Яндекс Маркета + «4-го»
нефиксированного источника Рунета; группировка по источникам; **выбор региона**;
исправление опечаток и синонимы; обоснованная стратегия обхода блокировок;
веб-интерфейс.

## Репозиторий

- `backend/` — Python 3.13, FastAPI. **Основной код, источник истины по логике.**
- `frontend/` — Next.js 16. Единственный фронт (старый `frontend2/` удалён —
  не собирался).
- `tz.md` — положение о хакатоне. `product.md` — продуктовый срез.
- `final_presa.pdf` — официальная методичка организаторов: запрет на любые внешние
  API (стр. 5), категории жюри: одежда / шины / оргтехника (стр. 3).

## Запуск

| Что | Команда |
|---|---|
| Backend | `cd backend && uv sync && uv run uvicorn pricepulse.main:app --reload` |
| Backend + L2-браузер | `uv sync --extra stealth` (ставит nodriver) |
| Spellcheck микросервис | `docker compose up -d --build spellcheck` (порт 8095) |
| SearXNG (4-й источник) | `docker compose up -d searxng` (порт 8080) |
| Redis (для кэша) | `docker compose up -d redis` |
| Frontend | `cd frontend && npm install && npm run dev` → http://localhost:3000 |
| Тесты | `cd backend && uv run pytest -q` — **62 passed** (на 23.05.2026) |
| Линт | `cd backend && uv run ruff check src/ tests/` |

## Конвенции

- Python 3.13, **uv везде** — `uv sync` / `uv run` / `uv lock`. В Dockerfile тоже:
  `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` + `uv pip install --system`.
  См. `backend/spellcheck/Dockerfile` как образец.
- Всё **async**. Pydantic v2, structlog.
- **Прежде чем добавлять зависимость** — web-research: актуальная ли, MIT-совместима ли,
  не противоречит ли методичке (никаких внешних API). См. историю — JamSpell отверг'нут
  ради SAGE, Firecrawl-cloud ради SearXNG, Patchright ради nodriver.
- ruff: `line-length = 120`, select `E/F/W/I/N/UP/B/ASYNC/S/C4/RUF`. Глобально
  игнорим `B008` (FastAPI Depends-default), `RUF001/002/003` (русский ≠ опечатка),
  `S101`. **`BLE001` не выбран** — не писать `# noqa: BLE001`.
- Per-file ignores: `S105` в `config.py` (dev-defaults), `N818` в `core/exceptions.py`
  (`RateLimited`/`CaptchaChallenge` — domain names), `S311` в `antibot/fingerprints.py`.
- **Никаких внешних API** в проде. Сторонние сервисы (SAGE, SearXNG, Ollama) запускаем
  только локально или на собственных серверах (методичка p.5).
- Коммиты **без Co-Authored-By: Claude** — проект как свой.

## Архитектура поиска

`POST /api/v1/search` → `SearchOrchestrator` (`orchestrator/search.py`):
- `routes/search.py` инжектит `cache=await get_search_cache()` + `limiter=await get_rate_limiter()`
  (синглтоны в `api/cache.py`).
- `normalize_query` (`enrichment/normalize.py`): _clean → brand-RapidFuzz →
  **SAGE /fix** (HTTP в spellcheck-сервис) → RU→EN translit → synonyms. Весь результат
  кэшируется в Redis по `sha1(raw)` — повтор запроса <2 мс.
- fan-out `asyncio.gather` по 4 источникам, изоляция краша в `_safe_call`.
- Адаптеры: `scrapers/{wb,ozon,yandex_market,runet}.py`, протокол — `scrapers/base.py`.
  Принимают `region_id: int = 213` (Yandex `lr`). **Реально использует регион только
  YandexMarket** (`build_search_url` + cookie `yandex_gid`). WB/Ozon/Runet принимают
  параметр для будущих расширений.
- 4-й источник: `scrapers/runet.py` — self-hosted SearXNG → топ-N не-маркетплейс URL →
  `curl_cffi` GET → JSON-LD `Product` парсер. Никаких внешних API.
- Группировка `SourceGroup` (count + min/avg/median) + Best-Deal ранжирование
  (`analytics/scoring.py`).
- Синоним-retry: если источник вернул пусто без ошибки и есть `alternates[]` —
  один повторный запрос с топ-синонимом.
- SSE-стриминг — `GET /api/v1/search/stream` (`api/routes/stream.py`).

## Anti-bot слой (L0 → L3, всё on-prem)

Каскад ленивой эскалации — дорогой слой включается только когда дешёвый
заблокирован.

- **L0** `antibot/ratelimit.py` — token-bucket на Redis (атомарный Lua-скрипт);
  при недоступности Redis деградирует в process-local bucket. Singleton —
  `api.cache.get_rate_limiter()`. Вшит в `_safe_call` оркестратора.
- **L1** `curl_cffi` 0.15 (TLS-impersonate). HTTP без браузера. Реально работает
  на WB (tenacity-retry на 429).
- **L2** `antibot/browser_pool.py` — стелс-браузер **nodriver** (CDP-direct).
  Синглтон `get_browser_pool()`, закрывается в lifespan. `antibot/browser_fetch.py` —
  L2-путь Ozon: прогрев сессии → решение slider-капчи → fetch composer-api тем же origin.
- **L3** `antibot/slider_solver.py` (OpenCV) + `antibot/vlm_solver.py` (Gemma 4 через
  локальный Ollama).
- `antibot/cascade.py` — `CascadeRouter`: per-source circuit-breaker. Эскалирует
  слой после 3 блокировок в окне 60 с. Платные ветки и `FeatureFlags` удалены.

**L2 caveat**: CSS-селекторы slider-капчи в `browser_fetch.py` помечены
`LIVE-CHECK` — проверить на сети хакатона.

## Spell-correction (SAGE микросервис)

`backend/spellcheck/` — изолированный docker-сервис.

- Модель: `ai-forever/sage-fredt5-distilled-95m` (Сбер, MIT, RUSpellRU F1 = 78.9 —
  бьёт GPT-4 на русском spell). 95M / 383 МБ. CPU-only torch wheel.
- Сервер: FastAPI + transformers (lazy load в lifespan). `GET /health`, `POST /fix`.
  Post-process убирает trailing-пунктуацию + lowercase под наш pipeline.
- Build через **uv** (`ghcr.io/astral-sh/uv:python3.11-bookworm-slim`), модель
  запекается в image, runtime offline (`HF_HUB_OFFLINE=1`). Образ ~2 ГБ.
- Клиент `enrichment/spellcheck_client.py` — async HTTP. `SPELLCHECK_URL=""`
  отключает шаг (graceful: лежит сервис — нормализация продолжается без коррекции).
- Latency: 450–900 мс CPU. Поэтому **нормализация кэшируется** в Redis по raw query —
  повторы <2 мс.
- См. `backend/docs/spellcheck-pipeline.md` для схемы и замеров.

## Статус по ТЗ (на 23.05.2026)

**Закрыто**:
- 4 источника (WB / Ozon / Я.Маркет / Runet — SearXNG-based).
- Группировка + min/avg/median.
- fan-out с изоляцией, SSE на бэке.
- Anti-bot слой L0→L3 (целиком on-prem).
- Sentiment-анализ, Prometheus-метрики.
- Синонимы — pymorphy3 + курируемый тезаурус.
- **Опечатки** — SAGE FRED-T5 микросервис.
- **Регион** — `SearchRequest.region_id` + проксирование через оркестратор; Yandex Market
  реально использует.
- Methodology-compliance (нет внешних API: Firecrawl-cloud / Megamarket-fallback /
  2captcha / Gemini / DeepSeek / Scrapfly / Apify / ZenRows — всё удалено).
- Кэш нормализации в Redis (cold ~900 мс → hit ~1–2 мс).
- CI: GitHub Actions (`ruff` + `pytest` + frontend `typecheck`).

**Открытые дыры (P0)**:
- **Регион в WB/Ozon/Runet** — параметр принимается, но игнорируется (только Yandex
  его реально применяет). Для WB нужна таблица `dest`-кодов по городам.
- **Категории жюри** — тезаурус ориентирован на электронику; методичка p.3 называет
  **Одежда / Шины / Оргтехника** — нужно расширить группы.
- **BPMN-схема** — есть только `docs/bpmn.placeholder.md`, диаграммы нет.
- **SSE на фронте** — `frontend/` шлёт обычный POST, готовый `/search/stream` не используется.
- **Характеристики товара** — реально заполнены только у WB.

## Ограничения и риски

- **nodriver — AGPL-3.0**, конфликтует с MIT при импорте в код. Изолирован в
  опциональной extra `stealth` + ленивый импорт. Решить до релиза.
- **ТЗ п. 7.5**: проект создан в окне 22.05 21:00 — 25.05 13:00. В репозитории
  есть код от 21.05 и раньше — риск дисквалификации (см. `product.md §5`).
- **Прокси**: Яндекс Маркету нужен резидентный RU-IP, бесплатного нет.
  В коде поле для прокси-URL есть, но фактически не подключено.

## MCP (только для разработки)

`.mcp.json` (gitignored, секреты) — серверы: `pricepulse` (свой), `context7`,
`playwright`, `sequential-thinking`, `postgres`. Шаблон — `.mcp.json.example`.
Раньше там был `firecrawl` — оставил, потому что MCP-сервер ≠ runtime-зависимость
(используется только агентом для разведки, не самим бэкендом).
