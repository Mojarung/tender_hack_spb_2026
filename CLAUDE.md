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
веб-интерфейс с детальной модалкой товара.

## Репозиторий

- `backend/` — Python 3.13, FastAPI. **Основной код, источник истины по логике.**
- `frontend/` — Next.js 16. Единственный фронт (старый `frontend2/` удалён —
  не собирался).
- `ozon_research/` — изолированный uv-проект, песочница с 13 диагностическими
  скриптами для Ozon. См. `ozon_research/README.md`. **Не зависит от backend.**
- `wb_research/` — то же самое для Wildberries.
- `tz.md` — положение о хакатоне. `product.md` — продуктовый срез.
- `final_presa.pdf` — официальная методичка организаторов: запрет на любые внешние
  API (стр. 5), категории жюри: одежда / шины / оргтехника (стр. 3).

## Запуск

| Что | Команда |
|---|---|
| Backend | `cd backend && uv sync && BROWSER_HEADLESS=false uv run uvicorn pricepulse.main:app --reload` |
| Spellcheck микросервис | `docker compose up -d --build spellcheck` (порт 8095) |
| SearXNG (4-й источник) | `docker compose up -d searxng` (порт 8080) |
| Redis (для кэша) | `docker compose up -d redis` |
| Postgres | `docker compose up -d postgres` |
| Frontend | `cd frontend && npm install && npm run dev` → http://localhost:3000 |
| Тесты | `cd backend && uv run pytest -q` — 60 passed (+2 fail зависят от поднятого spellcheck) |
| Линт | `cd backend && uv run ruff check src/ tests/` |
| Ozon research-песочница | `cd ozon_research && uv sync && uv run patchright install chromium` |
| WB research-песочница | `cd wb_research && uv sync` |

**Важно для Ozon**: `BROWSER_HEADLESS=false` обязательно — headless nodriver не
проходит Ozon antibot challenge, headed Chrome проходит автоматом за 3-5 сек.
Профиль cookies живёт в `backend/var/profiles/ozon/` (gitignored).

## Конвенции

- Python 3.13, **uv везде** — `uv sync` / `uv run` / `uv lock`. В Dockerfile тоже:
  `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` + `uv pip install --system`.
  См. `backend/spellcheck/Dockerfile` как образец.
- Всё **async**. Pydantic v2, structlog.
- **Прежде чем добавлять зависимость** — web-research: актуальная ли, MIT-совместима ли,
  не противоречит ли методичке (никаких внешних API).
- ruff: `line-length = 120`, select `E/F/W/I/N/UP/B/ASYNC/S/C4/RUF`. Глобально
  игнорим `B008` (FastAPI Depends-default), `RUF001/002/003` (русский ≠ опечатка),
  `S101`. **`BLE001` не выбран** — не писать `# noqa: BLE001`.
- Per-file ignores: `S105` в `config.py` (dev-defaults), `N818` в `core/exceptions.py`
  (`RateLimited`/`CaptchaChallenge` — domain names), `S311` в `antibot/fingerprints.py`.
- **Никаких внешних API** в проде. Сторонние сервисы (SAGE, SearXNG) запускаем
  только локально или на собственных серверах (методичка p.5).
- Коммиты **без Co-Authored-By: Claude** — проект как свой.

## Архитектура поиска

`POST /api/v1/search` → `SearchOrchestrator` (`orchestrator/search.py`):
- `routes/search.py` инжектит `cache=await get_search_cache()` + `limiter=await get_rate_limiter()`
  (синглтоны в `api/cache.py`).
- `normalize_query` (`enrichment/normalize.py`): _clean → brand-RapidFuzz →
  **SAGE /fix** (HTTP в spellcheck-сервис) → RU→EN translit → synonyms. Весь результат
  кэшируется в Redis по `sha1(raw)` — повтор запроса <2 мс.
- fan-out `asyncio.gather` по источникам, изоляция краша в `_safe_call`.
- Адаптеры: `scrapers/{wb,ozon,yandex_market,runet}.py`, протокол — `scrapers/base.py`.
- 4-й источник: `scrapers/runet.py` — self-hosted SearXNG → топ-N не-маркетплейс URL →
  `curl_cffi` GET → JSON-LD `Product` парсер. Никаких внешних API.
- Группировка `SourceGroup` (count + min/avg/median) + Best-Deal ранжирование
  (`analytics/scoring.py`).
- Синоним-retry: если источник вернул пусто без ошибки и есть `alternates[]` —
  один повторный запрос с топ-синонимом.
- SSE-стриминг — `GET /api/v1/search/stream` (`api/routes/stream.py`). Фронт
  использует его через `EventSource` в `frontend/src/lib/api.ts:searchStream`.

**TEMP (май 2026)**: оркестратор в `_registry` сейчас включает **только Ozon** —
другие 3 источника закомментированы (с `# noqa: F401` на импортах) пока обкатываем
новый Ozon-путь. Ревёрт = un-comment блок в `orchestrator/search.py:_registry` +
синхронно `backend/tests/test_health.py:test_search_empty_groups`.

## Ozon — текущая архитектура (май 2026)

Полная переписка после того как чистый `curl_cffi` mobile-API перестал пробивать
WAF Ozon. Источник истины — `backend/src/pricepulse/{scrapers/ozon.py,antibot/browser_fetch.py,antibot/browser_pool.py}`.
Песочница для диагностики и регрессов — `ozon_research/`.

### Поток одного запроса

```
search() ──► OzonCookieWarmer.get_cookies()           [12h TTL, диск-кэш]
       │       │ если cache miss или force-refresh:
       │       └──► nodriver (headed Chrome) →
       │             navigate(ozon.ru/) + navigate(ozon.ru/search/?text=ноутбук)
       │             → 14-15 cookies экспортятся
       │
       ├──► _search_l1(query, cookies)                [curl_cffi chrome impersonate]
       │     │ POST /composer-api.bx/page/json/v2?url=/search/?text=...
       │     │ on 403 → fallback на entrypoint-api.bx
       │     └─► stubs или None
       │
       ├──► если None: invalidate cookies + force-refresh + retry L1
       │
       ├──► если всё ещё None: _search_l2 (L2 fallback)
       │     └──► fetch_ozon_via_browser → tab навигирует на /search/?text=...
       │           → same-origin fetch composer-api → widgetStates
       │
       └──► _enrich_all(stubs, cookies)               [параллельно через asyncio.gather]
             │
             ├─► _enrich_one_l1 (curl_cffi):
             │     • PDP fetch → backfill name/sku/image/price из widgets
             │     • full-chars cascade: characteristicsList →
             │       webProductCharacteristics → webShortCharacteristics →
             │       pdpAtomicCharacteristics
             │     • reviews fetch: ?layout_container=reviewshelfpaginator
             │       &sort=published_at_desc&limit=20
             │
             └─► если name всё ещё None (L1 PDP blocked) →
                 _enrich_one_browser (gather 3 fetch_ozon_via_browser):
                 • PDP + characteristicsList + reviews параллельно
                 • BrowserPool semaphore (max_tabs=4) бьёт по 4 одновременно
```

### Ключевые модули

**`antibot/browser_pool.py`** — singleton nodriver Browser, ленивый запуск,
persistent `user_data_dir=settings.ozon_profile_dir` (по умолчанию `var/profiles/ozon`).
`STEALTH_INIT` инжектится на каждый таб через `Page.addScriptToEvaluateOnNewDocument`
(navigator.webdriver, navigator.languages, hardwareConcurrency, deviceMemory,
WebGL vendor/renderer, canvas LSB-noise, Notification quirk). Авто-recovery:
`_is_dead_browser_error()` ловит `ConnectionClosedError` (если юзер вручную
закрыл окно), `_reset_browser()` тушит мёртвый instance — следующий `acquire()`
запускает Chrome с нуля. Pool exposes `.browser` property — callers не лезут в
приватный `_browser`.

**`antibot/browser_fetch.py`** — Ozon-специфичные helpers:
- `OzonCookieWarmer` синглтон с TTL + async lock + disk persistence
  (`var/profiles/ozon/ozon_cookies.json`). `_warm()` навигирует браузер на
  ozon.ru/ + search-страницу, экспортирует cookies (пропускает мёртвый pool —
  triggers reset).
- `fetch_ozon_via_browser(sub_path)` — same-origin fetch composer-api из таба
  warmed на чистый URL (без `layout_container=` параметров — иначе Ozon
  рендерит пустой shell). Возвращает parsed `widgetStates`.
- `textrs_to_str(node)` — рендер 2026 формата `{textRs:[{type, content}]}` в str.
- `chars_via_structural(widgets)` — структурный walker всех виджетов,
  ищет `{title|name + values}` пары, обрабатывает text-runs, blacklist CTA
  (`Перейти`, `Купить`, `В корзину` …). Не зависит от widget-key имени.
- `backfill_from_pdp(offer, widgets)` — `webProductHeading.title` → name,
  `webProductMainWidget.sku` → sku, `webGallery.images[]` → image + images[],
  `webPrice/webOzonAccountPrice` → price, `webReviewProductScore.{score,reviewsCount}`
  → rating + reviews_count.
- `extract_reviews(widgets, limit)` — строгий whitelist widget-keys
  (`webListReviews-*`, `webReviewList-*`, `reviewshelfpaginator-*`,
  `weblistcomments-*`), min 30 символов, blacklist CTA-текстов, dedup по
  `(author, text[:120])`, парсит `photos[]`/`images[]`/`content.photos`/
  `content.images`/`media`, `published_at`/`createdAt`/`date`.

**`scrapers/ozon.py`** — оркестрирует поток выше. Headers под dweb_client
(Sec-Fetch-*, Referer, Accept, x-o3-app-name), curl_cffi
`impersonate="chrome"`. Контейнеры в каскаде full-chars и сорт-параметры
отзывов — module-level константы.

### Настройки (config.py)

- `browser_headless: bool = True` — переопределить на `false` (env
  `BROWSER_HEADLESS=false`) для headed Chrome. **Headless не пробивает!**
- `ozon_profile_dir: str = "var/profiles/ozon"` — где живут warmed cookies
  и Chrome profile. В Docker привязать как volume.
- `ozon_cookie_ttl_sec: int = 12 * 3600` — после этого force-refresh через nodriver.
- `ozon_browser_path: str = ""` — override Chrome binary (для Yandex Browser etc).

### Frontend (модалка)

`frontend/src/components/ProductDetailModal.tsx` — клик по `ProductCard` открывает
portal-модалку:
- Карусель картинок (главное фото слева + thumbnails внизу + стрелки/счётчик
  + keyboard ←→)
- Таблица характеристик (полный спек, скроллится в parent body)
- Список отзывов с `<select>` сортировки (newest/oldest/rating↓/rating↑),
  `max-h-[420px] overflow-y-auto`, фото-thumbnails 56×56 (клик в новой вкладке
  на full-size, через `/api/v1/image-proxy` кэшируются в MinIO), дата ru-RU
  long format.
- Footer с ценой + "Открыть на Ozon" external link.
- Esc + click-outside close, `document.body.style.overflow = "hidden"` на время.
- `createPortal(document.body)`, framer-motion-анимация.

### Ozon research-песочница

`ozon_research/` — собственный uv-проект (`pyproject.toml`, `.venv`, `.python-version`),
ноль зависимостей от backend. 13 пронумерованных скриптов покрывают весь путь
диагностики:

- `01_l1_baseline.py` — старое прод-поведение
- `02_l1_hardened.py` — все недостающие хедеры + TLS-каскад
- `03_l1_entrypoint_fallback.py` — composer-api vs entrypoint-api
- `04_reviews_endpoint.py` / `05_characteristics_endpoint.py` — отдельные слои
- `06_full_pipeline.py` — end-to-end L1
- `07_slider_solver_canny.py` — OpenCV solver для слайдера (если когда-то нужно)
- `08_human_drag.py` — cubic-Bezier человекоподобный драг
- `09_patchright_l2.py` — альтернатива nodriver
- `10_yandex_clickthrough.py` — fallback через Yandex SERP с реферером
- `11_diagnose.py` — матрица 3 хоста × 3 TLS × 2 header-mode, говорит где блок
- `12_nodriver_pro.py` — **флагман**, реальный Chrome через nodriver, persistent
  profile. Используется как ручной cookie-warmer и регресс-тест
- `13_warm_cookies_to_curl.py` — после 12 импортирует cookies в curl_cffi для
  скорости HTTP-only пути

См. `ozon_research/README.md` для полной инструкции.

## Wildberries — текущая архитектура

`scrapers/wb.py` (basic search) + `scrapers/wb_feedbacks.py` (отзывы):

- Public JSON API `https://search.wb.ru/exactmatch/ru/common/v18/search` — без auth,
  без капчи. Rate-limit ~10 RPS per IP, на 429 ставим cooldown 120 с (`_open_cooldown`).
- Параметры включают `dest=-1257786` (Москва), `appType=1`, `curr=rub`, `regions=...`,
  `spp=30`. `tenacity` retry на не-429 ошибки.
- Картинка одна (cover) — `wb_basket.image_url(nm_id)` через basket-shard math.
- Отзывы: `feedbacks{1,2}.wb.ru/feedbacks/v1/{imt_id}` (нужен `imt_id` = поле `root`
  из search response). Возвращает до 1000 отзывов, мы тримим до limit.

**Известные дыры (см. `wb_research/` для детального ресерча и рекомендаций)**:
- Нет fetch'а полного card.json (`basket-{NN}.wbbasket.ru/vol{V}/part{P}/{nm}/info/ru/card.json`)
  → нет характеристик и описания
- Нет загрузки полной галереи изображений
- Используется `v1` отзывов вместо `v2` (v2 содержит `photos[]` и `video{}` блоки)
- Нет извлечения photo URLs из отзывов
- Захардкожен Moscow `dest`, region_id игнорируется

## Spell-correction (SAGE микросервис)

`backend/spellcheck/` — изолированный docker-сервис.

- Модель: `ai-forever/sage-fredt5-distilled-95m` (Сбер, MIT, RUSpellRU F1 = 78.9 —
  бьёт GPT-4 на русском spell). 95M / 383 МБ. CPU-only torch wheel.
- Сервер: FastAPI + transformers (lazy load в lifespan). `GET /health`, `POST /fix`.
- Build через uv, модель запекается в image, runtime offline (`HF_HUB_OFFLINE=1`).
- Клиент `enrichment/spellcheck_client.py` — async HTTP. `SPELLCHECK_URL=""`
  отключает шаг (graceful).
- Нормализация кэшируется в Redis по raw query — повторы <2 мс.

## Что закрыто

- 4 источника (WB / Ozon / Я.Маркет / Runet — SearXNG-based), временно
  включён только Ozon под soak-test.
- **Ozon full-pipeline**: cookie warm-up через headed nodriver, L1+L2 enrichment
  cascade, полные характеристики, отзывы с фото, рейтинг + reviews_count.
- Группировка + min/avg/median.
- Fan-out с изоляцией, SSE на бэке и **на фронте** (EventSource).
- Browser-pool с auto-recovery на ConnectionClosedError.
- Sentiment-анализ, Prometheus-метрики.
- Синонимы — pymorphy3 + курируемый тезаурус.
- Опечатки — SAGE FRED-T5 микросервис.
- Регион — `SearchRequest.region_id` + проксирование через оркестратор.
- Кэш нормализации в Redis (cold ~900 мс → hit ~1–2 мс).
- **Frontend модалка товара** с галереей + полными чарами + отзывы (сортировка,
  фото-thumbnails).
- Image-proxy на MinIO (картинки кэшируются локально, `/api/v1/image-proxy?url=...`).
- CI: GitHub Actions (`ruff` + `pytest` + frontend `typecheck`).

## Открытые дыры (P0)

- **WB enrichment** — нет полного card.json, нет галереи, нет v2 отзывов с фото.
  План в `wb_research/`.
- **Регион в WB/Ozon/Runet** — параметр принимается, но WB и Runet игнорируют.
  Для WB нужна таблица `dest`-кодов (есть top-12 в `wb_research/`).
- **Категории жюри** — тезаурус ориентирован на электронику; методичка p.3 называет
  **Одежда / Шины / Оргтехника** — нужно расширить группы.
- **BPMN-схема** — есть только `docs/bpmn.placeholder.md`, диаграммы нет.
- **Other-than-Ozon sources** — закомментированы в orchestrator, нужно вернуть
  после стабилизации Ozon-пути.

## Ограничения и риски

- **nodriver — AGPL-3.0**, в core deps (раньше был optional extra). Для
  внутреннего хакатона ОК — публичный продукт мы не продаём.
- **Headed Chrome в проде** — на Linux-сервере без X нужен Xvfb. На Windows
  работает из коробки.
- **Прокси**: Яндекс Маркету нужен резидентный RU-IP, бесплатного нет.
- **`basket-{NN}.wbbasket.ru`** — WB периодически добавляет новые шарды (сейчас
  до 35, в коде до 21). `wb_basket.py` нужно обновить + добавить ±5 fallback loop.

## MCP (только для разработки)

`.mcp.json` (gitignored, секреты) — серверы: `pricepulse` (свой), `context7`,
`playwright`, `sequential-thinking`, `postgres`. Шаблон — `.mcp.json.example`.

## Патчи зависимостей

`backend/.venv/Lib/site-packages/nodriver/cdp/network.py` строка 1345 содержит
сырой байт `0xb1` без encoding-декларации — Python на cp1251 (Windows) падает
при импорте. Фикс одной строкой:

```python
import pathlib
p = pathlib.Path('.venv/Lib/site-packages/nodriver/cdp/network.py')
p.write_bytes(p.read_bytes().replace(b'\xb1', b'\xc2\xb1', 1))
```

Запускать после каждого `uv sync` пока баг не пофиксят upstream.
