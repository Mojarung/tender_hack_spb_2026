# Runet research — Yandex shopping SERP tab

Изолированная песочница: свой `pyproject.toml`, свой `.venv`, нулевая связь с
`backend/`. Mirror of `wb_research/` and `ozon_research/` layout.

## Цель

Парсинг товаров с **Yandex Поиск → вкладка «Покупки»** через headed-Chrome
с антислежкой (`nodriver`). Та же модель что у Ozon/WB:

1. Поднимаем persistent stealth-браузер (профиль в `.profile_yandex/`)
2. Открываем `yandex.ru/search/?text=<q>`
3. Кликаем на вкладку «Покупки» (или сразу URL `?service=tovary` если работает)
4. Парсим карточки: name / price / image / url / brand / rating / reviews_count
5. По возможности дотягиваем характеристики на странице товара

Результат — `RunetScraper` в `backend/src/pricepulse/scrapers/runet_yandex.py`
(существующий `runet.py` использует SearXNG — оставим как fallback).

## Скрипты

| # | Файл | Что проверяет |
|---|---|---|
| 01 | `01_serp_baseline.py` | Открыть Yandex SERP, проверить что не получили captcha |
| 02 | `02_purchases_tab.py` | Найти и кликнуть вкладку «Покупки» |
| 03 | ... | (заполняем по ходу) |

## Запуск

```bash
cd runet_research
uv sync
uv run python 01_serp_baseline.py "iphone 15"
```
