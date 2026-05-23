# PricePulse — backend

FastAPI / Python 3.13, async-first. Источник истины по логике поиска и anti-bot
стеку — **[`CLAUDE.md`](../CLAUDE.md)** в корне репозитория. Запуск, переменные
окружения и быстрый старт — **[`README.md`](../README.md)** в корне.

## Структура

```
src/pricepulse/
  api/                    # FastAPI routes, cache + rate-limiter singletons
  orchestrator/search.py  # SearchOrchestrator (fan-out, group, rank)
  scrapers/               # wb, ozon, yandex_market, runet (+ base protocol)
  enrichment/             # normalize, spellcheck client, thesaurus, translit
  antibot/                # ratelimit, browser_pool, cascade, vlm_solver
  analytics/              # scoring (Best-Deal), sentiment
  core/                   # exceptions, models
  config.py
spellcheck/               # SAGE FRED-T5 микросервис (отдельный Dockerfile)
tests/                    # 62 passed
```

## Локальный запуск

```bash
uv sync                                              # base
uv sync --extra stealth                              # +nodriver (L2)
cp .env.example .env
uv run uvicorn pricepulse.main:app --reload          # http://localhost:8000/docs
uv run pytest -q                                     # 62 passed
uv run ruff check src/ tests/                        # clean
```
