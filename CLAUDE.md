# CLAUDE.md — PricePulse

Контекст для AI-агента: чтобы при переезде на другой компьютер не терять понимание
проекта и принятых решений. Источник истины по требованиям — `tz.md` и `product.md`.

## Что это

PricePulse — интеллектуальный агрегатор цен для **Портала поставщиков**
(zakupki.mos.ru). Проект хакатона **Tender Hack SPb 2026**, защита **24.05.2026**.
Задача: собрать цены товара с Wildberries, Ozon, Яндекс Маркета + «4-го»
нефиксированного источника Рунета; группировка по источникам; исправление опечаток
и синонимы; обоснованная стратегия обхода блокировок; веб-интерфейс.

## Репозиторий

- `backend/` — Python 3.13, FastAPI. **Основной код, источник истины по логике.**
- `frontend/` — Next.js 16. **Рабочий** фронт, его и развивать.
- `frontend2/` — Vite+React. **НЕ собирается** (отсутствует `src/lib/`). Не запускать,
  жюри не показывать — либо чинить, либо удалить.
- `tz.md` — положение о хакатоне (ТЗ, раздел V — задачи). `product.md` — продуктовый
  срез и Definition of Done.

## Запуск

| Что | Команда |
|---|---|
| Backend | `cd backend && uv sync && uv run uvicorn pricepulse.main:app --reload` |
| Backend + L2-браузер | `uv sync --extra stealth` (ставит nodriver) |
| Frontend | `cd frontend && npm install && npm run dev` → http://localhost:3000 |
| Тесты | `cd backend && uv run pytest -q` — 35 passed (на 22.05.2026) |
| Линт | `cd backend && uv run ruff check src/` |

## Конвенции

- Python 3.13, менеджер пакетов — **uv**. Всё **async**. Pydantic v2, structlog.
- ruff: line-length 100, select `E/F/W/I/N/UP/B/ASYNC/S/C4/RUF`. **`BLE` не включён** —
  не писать `# noqa: BLE001` (ruff пометит как unused).
- **Free-mode по умолчанию**: killswitch `FEATURES_ALLOW_PAID=false` + гранулярные
  `FEATURE_USE_*`. Платная ветка не выполняется, пока killswitch false (`core/features.py`).
- Пред-существующий lint-долг (не трогать без запроса): `S105` в `config.py`
  (дефолты-«пароли»), `RUF001/RUF002` в `vlm_solver.py`/`proxy_pool.py`/`ozon.py`.

## Архитектура поиска

`POST /api/v1/search` → `SearchOrchestrator` (`orchestrator/search.py`):
- fan-out `asyncio.gather` по 4 источникам, изоляция краша каждого в `_safe_call`;
- адаптеры: `scrapers/{wb,ozon,yandex_market,runet,megamarket}.py`, протокол —
  `scrapers/base.py`; Runet (Firecrawl) при пустой выдаче падает на Megamarket;
- группировка `SourceGroup` + Best-Deal ранжирование (`analytics/scoring.py`);
- SSE-стриминг — `GET /api/v1/search/stream` (`api/routes/stream.py`).

## Anti-bot слой (переделан 22.05, ужат под методичку 23.05)

Каскад **L0→L3**, ленивая эскалация — дорогой слой включается только когда дешёвый
заблокирован. Все слои бегут **на нашей инфраструктуре** — никаких внешних API
(методичка `final_presa.pdf` p.5).

- **L0** `antibot/ratelimit.py` — token-bucket на Redis (атомарный Lua-скрипт);
  при недоступности Redis деградирует в process-local bucket. Вшит в `_safe_call`
  оркестратора — каждый запрос ждёт токен (`wb_rpm`/`ozon_rpm`/… из `config.py`).
- **L1** — `curl_cffi` 0.15 (TLS-impersonate). HTTP без браузера. Реально работает
  на WB (tenacity-retry на 429).
- **L2** `antibot/browser_pool.py` — стелс-браузер **nodriver** (CDP-direct, без
  WebDriver — обходит automation-protocol fingerprinting). Синглтон
  `get_browser_pool()`, закрывается в lifespan `main.py`. `antibot/browser_fetch.py` —
  L2-путь Ozon: прогрев сессии → решение slider-капчи → fetch composer-api тем же
  origin (переиспользует L1-парсеры).
- **L3** `antibot/slider_solver.py` (OpenCV) + `antibot/vlm_solver.py` (Gemma 4 через
  локальный Ollama). Free, на нашем CPU/GPU.
- `antibot/cascade.py` — `CascadeRouter`: per-source circuit-breaker, эскалирует
  слой после 3 блокировок источника в окне 60 с. Вшит в `_safe_call`. Без флагов —
  платные ветки удалены целиком.

**Удалено 23.05 по методичке**: `antibot/captcha.py` (2captcha — внешний API),
`scrapers/megamarket.py` (марекетплейс — запрещён как 4-й источник), Firecrawl-cloud
в `runet.py`, Gemini/DeepSeek/Scrapfly/Apify/ZenRows ключи в `config.py`/`.env.example`,
все `FEATURE_USE_*` / `FEATURES_ALLOW_PAID` / `cost_cap_usd`.

**4-й источник** — `scrapers/runet.py`: self-hosted **SearXNG** (URL discovery) →
фильтр маркетплейсов → `curl_cffi` GET → JSON-LD `Product` парсер. Конфиг SearXNG
с `format=json` — `backend/searxng/settings.yml`, монтируется в docker-compose.

**Важно про L2 Ozon**: CSS-селекторы slider-капчи в `browser_fetch.py` помечены
`LIVE-CHECK` — проверить на сети хакатона. Геометрия `solve_slider` протестирована,
DOM-привязка к живому Ozon — нет.

**Решения по инструментам** (исследование 22.05.2026): nodriver выбран по бенчмарку
(28/31 vs Cloudflare, 0 хард-блоков). Camoufox отвергнут — beta, год без поддержки.
Patchright отвергнут — Playwright-инструменты палятся на automation-protocol fingerprint.

## Статус по ТЗ (аудит 22.05.2026, обновлён 23.05)

**Закрыто**: 4 источника, группировка + счётчики + **min/avg/median**-цена, fan-out
с изоляцией, SSE на бэке, anti-bot слой L0→L3 (целиком on-prem), sentiment-анализ,
Prometheus-метрики, **синонимы** — pymorphy3 + курируемый тезаурус
(`enrichment/morphology.py` + `synonym_thesaurus.py`), **methodology-compliance**
(нет внешних API: Firecrawl-cloud / 2captcha / Gemini / DeepSeek / Scrapfly / Apify /
ZenRows удалены; Megamarket не является 4-м источником; 4-й источник — self-hosted
SearXNG + JSON-LD).

**Открытые дыры (P0)**:
- **Регион** — `SearchRequest` пока без `region`, ТЗ методички (p.4) требует выбор.
- **Категории жюри** — тезаурус ориентирован на электронику; методичка p.3 называет
  **Одежда / Шины / Оргтехника** — нужно расширить группы.
- **BPMN-схема** — есть только `docs/bpmn.placeholder.md`, диаграммы нет.
- **SSE на фронте** — `frontend/` шлёт обычный POST, готовый `/search/stream` не используется.
- **Характеристики товара** — реально заполнены только у WB.

**Опечатки** — закрыты транформером **SAGE FRED-T5 distilled-95M** от Сбера
(MIT, RUSpellRU F1 = 78.9 — бьёт GPT-4 на русском spell). Сервис в
`backend/spellcheck/` (FastAPI + transformers + torch CPU, ~2 ГБ image),
клиент `enrichment/spellcheck_client.py` ходит HTTP. Pipeline `normalize_query`:
brand-RapidFuzz → SpellCheck (контекстная RU-коррекция) → translit → синонимы.
`SPELLCHECK_URL=""` отключает шаг (graceful: если сервис лежит — `normalize_query`
идёт без коррекции). Предыдущая JamSpell-реализация выпилена — small-модель давала
wrong-corrections («стирлная → сильная»).

## Ограничения и риски

- **nodriver — AGPL-3.0**, конфликтует с MIT-лицензией проекта при импорте в код.
  Изолирован в опциональной extra `stealth` + ленивый импорт. Решить до релиза.
- **ТЗ п. 7.5**: проект должен быть создан целиком в окне 22.05 21:00 — 25.05 13:00.
  В репозитории есть код от 21.05 и ранее — риск дисквалификации (см. `product.md §5`).
- **Прокси**: Яндекс Маркету нужен резидентный RU-IP, бесплатного нет — слой
  cost-gated за `FEATURE_USE_PAID_PROXIES`.

## MCP (для разработки)

`.mcp.json` (gitignored, секреты) — серверы: `pricepulse` (свой), `context7`,
`playwright`, `sequential-thinking`, `firecrawl`, `postgres`. Шаблон — `.mcp.json.example`.
