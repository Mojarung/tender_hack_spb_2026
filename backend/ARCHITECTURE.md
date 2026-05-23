# PricePulse — Архитектура backend

Источник истины по архитектуре и принятым решениям — **[`CLAUDE.md`](../CLAUDE.md)**
в корне репозитория. Этот файл оставлен как шорткат и описывает только высокоуровневую
схему запроса.

## Схема `/api/v1/search`

```
POST /api/v1/search { query, region_id }
            │
            ▼
   SearchOrchestrator (orchestrator/search.py)
            │
            ├── normalize_query  (enrichment/normalize.py)
            │     clean → brand-fuzzy → SAGE FRED-T5 (HTTP к spellcheck)
            │     → translit RU/EN → synonyms (pymorphy3 + thesaurus)
            │     → cached in Redis by sha1(raw)
            │
            ├── asyncio.gather  (fan-out по 4 источникам)
            │     ┌── scrapers/wb.py            (curl_cffi L1)
            │     ├── scrapers/ozon.py          (L2 nodriver при detect)
            │     ├── scrapers/yandex_market.py (region via lr + cookie)
            │     └── scrapers/runet.py         (SearXNG → JSON-LD)
            │     каждый завёрнут в _safe_call → не валит соседей,
            │     ждёт токен у antibot/ratelimit.py (Redis bucket)
            │
            ├── group by source (SourceGroup: count, min, avg, median)
            │
            └── rank top_deals (analytics/scoring.py)
                  → 200 OK { groups[], top_deals[] }
```

`GET /api/v1/search/stream` — тот же оркестратор, но через SSE
(`api/routes/stream.py`); поддерживает `?nofix=1`.

## Принципы

- **Async-first** — любой I/O через asyncio.
- **Изоляция источников** — падение/блок одного адаптера не валит соседей.
- **Anti-bot снаружи адаптеров** — общий слой `antibot/`, адаптер дёргает «дай страницу».
- **Кэш — first-class citizen** — нормализация и поиск кэшируются в Redis.
- **Никаких внешних API в проде** — методичка `final_presa.pdf` p.5.
