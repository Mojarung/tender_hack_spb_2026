# PricePulse — backend

Backend для сервиса агрегированного поиска цен. Полная архитектура — в [ARCHITECTURE.md](./ARCHITECTURE.md), требования — в [../product.md](../product.md), оригинал ТЗ — в [../tz.md](../tz.md).

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
uv run pytest                  # unit-тесты
uv run pytest -m live          # live-тесты, бьющие в реальные источники
```

## Структура

См. [ARCHITECTURE.md, раздел 4](./ARCHITECTURE.md#4-структура-каталогов).
