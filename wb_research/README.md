# WB research — экспериментальные парсеры (отдельный uv-проект)

Изолированная песочница на уровне репо: свой `pyproject.toml`, свой `.venv`,
нулевая связь с `backend/`. Производственный код в
`backend/src/pricepulse/scrapers/{wb,wb_feedbacks}.py` НЕ трогается.

Каждый файл — самостоятельный smoke-скрипт (не pytest), печатает цветной
лог и сохраняет JSON в `_out/`. Скрипты НЕ импортируют `pricepulse.*`.

## Что показал ресерч (TL;DR из 3 параллельных агентов)

**Главное**: WB технически НАМНОГО проще чем Ozon. Public JSON API
(`search.wb.ru/v18`, `basket-NN.wbbasket.ru/.../card.json`,
`feedbacks{1,2}.wb.ru/feedbacks/v2/`) — открытый, без капчи, без JA3-чека.
Плейн `httpx` справляется. Никакого браузера, прогретых cookies, slider
solver'ов как у Ozon не надо.

**Что прод-`wb.py` сейчас НЕ делает** (и что хочется добавить):

1. **Полные характеристики и описание** товара — живут в `basket-{NN}.wbbasket.ru/vol{V}/part{P}/{nm}/info/ru/card.json`. Прод использует только `search.wb.ru`, где их нет. Решает скрипт **02**.

2. **Полная галерея фото** — `card.json → media.photo_count` даёт N, а URL `/images/big/{1..N}.webp`. Решает **05**.

3. **Отзывы v2 с фото и видео** — `feedbacks{1,2}.wb.ru/feedbacks/v2/{imt_id}` (вместо v1) возвращает `photos:[{key:"6/uuid", ...}]` и `video:{id:"3/uuid", ...}`. URL фотки: `https://feedback-{shard:02d}.wbbasket.ru/{uuid}/{ms,fs}.webp`. URL видео — HLS playlist `index.m3u8` (не mp4!). Решает **04**.

4. **Регион (dest код)** — захардкожена Москва. Карта Yandex `lr → WB dest` для топ-12 городов уже зашита в `_common.YANDEX_LR_TO_WB_DEST`. Тест эффекта — **08**.

5. **curl_cffi fallback** — если WB когда-нибудь включит JA3-чек, нужен план Б. Сейчас не нужен, но проверить можно — **09**.

**Антибот в 2026** (агент №2 подтвердил):
- Public catalog API: ~10 RPS per IP без 429, дальше — соorцать
- `*.wbbasket.ru` (basket-CDN) — static-CDN, практически без лимитов
- Cookies (`__wbl`, `_wbauid`, `BasketUID`) — НЕ требуются на JSON-эндпоинтах
- JA3/TLS check — на public API нет
- Captcha — нет (HTML-страницы изредка показывают, но мы их не парсим)
- Mobile API закрыто PoW + Play Integrity → не суёмся

**Источники для каждого вывода — см. блок "Сводный ресерч" в самом конце README.**

## Установка

```powershell
cd wb_research               # из корня репо
uv sync                      # создаст ./.venv с httpx + curl_cffi + orjson + tenacity + nodriver
```

**Update май 2026**: WB включил **Page Guard / PoW** на `search.wb.ru/v18` —
плейн httpx начал отдавать `429` с `status-no-id: PG-41-XS` и
`Access-Control-Expose-Headers: x-pow`. Теперь нужен либо PoW-solver, либо —
проще — **прогреть cookies через настоящий браузер**. См. скрипты 11/12/13.

## Что запускать и в каком порядке

| # | Скрипт | Что делает | Аргументы |
|---|---|---|---|
| 01 | `01_search_baseline.py` | Public search v18 — текущее прод-поведение | `"запрос"` |
| 02 | `02_card_detail.py` | Full card.json (chars + description + imt_id) | `<nm_id>` (из 01) |
| 03 | `03_imt_resolution.py` | Сравнивает 3 способа получить imt_id (search.root vs card.json vs card.wb.ru/v2) | `"запрос"` |
| 04 | `04_feedbacks_v2.py` | Отзывы v2 с photo_urls + video_urls | `<imt_id>` (из 02 или 03) |
| 05 | `05_gallery.py` | Полная галерея с HEAD-проверкой каждого URL | `<nm_id>` |
| **06** | `06_full_pipeline.py` | **Главный**. End-to-end demo: search → 5 продуктов × (chars + gallery + reviews-with-photos) | `"запрос"` |
| 07 | `07_429_handling.py` | Rate-limit probe — НЕ ЗАПУСКАТЬ ЧАСТО, триггерит блок | `[rps=15] [duration_s=20]` |
| 08 | `08_region_dest.py` | Тот же запрос против 12 dest-кодов — есть ли price spread? | `"запрос"` |
| 09 | `09_curl_cffi_fallback.py` | A/B httpx vs curl_cffi (на случай JA3-чека) | `"запрос"` |
| 10 | `10_diagnose.py` | Health-check: search + card + feedbacks одним прогоном | (без аргументов) |
| **11** | `11_browser_warmer.py` | **PoW-solver shortcut**: headed Chrome через nodriver → same-origin fetch search.wb.ru → экспорт cookies в `_out/wb_cookies.json` | `"запрос"` |
| **12** | `12_warm_cookies_to_curl.py` | После 11 — пробует A/B httpx + curl_cffi (chrome/chrome131) с экспортированными cookies. Скажет какой клиент пробил | `"запрос"` |
| 13 | `13_pow_inspector.py` | CDP-инспектор: логирует ВСЕ x-pow/x-bx-*/x-wb-* заголовки и cookies, которые отправляет реальный браузер. Для понимания КАК WB подписывает запросы | `"запрос"` |
| **14** | `14_browser_search_pool.py` | **Production pattern**. `WBBrowserSearch` класс с persistent tab. Один nodriver-таб на wildberries.ru держится живым, каждый search() делает same-origin fetch → JS считает PoW. ~300-700 ms steady-state. | `"q1" "q2" "q3"` |

### Обычный сценарий

```powershell
cd wb_research
uv sync

# 1. Поиск — получаем nm_id и root (= imt_id):
uv run python 01_search_baseline.py "шины 205 55 R16"
# в JSON-выводе берём product[0].id (nm_id) и product[0].root (imt_id)

# 2. Полная карточка — характеристики и описание:
uv run python 02_card_detail.py 147319365

# 3. Сверяем imt_id через 3 пути (опционально):
uv run python 03_imt_resolution.py "шины 205 55 R16"

# 4. Отзывы с фото:
uv run python 04_feedbacks_v2.py 50988792

# 5. Галерея:
uv run python 05_gallery.py 147319365

# 6. Главный демо-скрипт — всё за один прогон:
uv run python 06_full_pipeline.py "ноутбук lenovo"
# → _out/<ts>_06_full_pipeline_ok.json — готовый payload для интеграции в прод
```

### Если что-то ломается

1. Запусти `10_diagnose.py` — он покажет какой слой упал
2. Если 429 с `status-no-id: PG-41-XS` → **Page Guard / PoW сработал**:
   ```powershell
   uv run python 11_browser_warmer.py "ноутбук"   # один раз — Chrome пройдёт JS challenge
   uv run python 12_warm_cookies_to_curl.py "ноутбук"   # быстрый HTTP с warmed cookies
   ```
   Если 12 показывает 200 — wire это в прод-`wb.py`. Если нет — запусти 13 чтобы
   понять что именно WB ждёт (x-pow header? rotating cookie?).
3. Если 429 без PG-41 → обычный rate-limit, подожди 2 минуты
4. Если 403 на search.wb.ru → запусти `09_curl_cffi_fallback.py`, если curl_cffi
   с `chrome131` пробивает а httpx — нет, значит WB включил JA3 в проде
5. Если card.json постоянно 404 → проверь обновился ли basket-shard cascade в
   `_common.basket_for()` (WB добавляет новые shards раз в несколько месяцев,
   сейчас знаем до 35)

## Что увидишь в `_out/`

- `<ts>_01_ok.json` — массив products со всеми полями search-ответа
- `<ts>_02_card_ok.json` — `{characteristics: [[group, name, value], ...], raw: <card.json>}`
- `<ts>_04_reviews_ok.json` — `{reviews: [{photo_urls, video_urls, ...}], total, valuation, ...}`
- `<ts>_05_gallery.json` — `{count, urls: [...]}` после HEAD-верификации
- `<ts>_06_full_pipeline_ok.json` — финальный артефакт для демо
- `<ts>_10_diagnose.json` — table-format health для копипасты в issue

## План интеграции в прод

(не делать сейчас — сначала проверь руками)

1. **`backend/src/pricepulse/scrapers/wb_basket.py`** — обновить `_RANGES` до shard 35 + добавить ±5 fallback loop (copy from `02_card_detail.py:_fetch_card`).

2. **`backend/src/pricepulse/scrapers/wb_card.py`** (НОВЫЙ) — `fetch_card_json(nm_id)` и `parse_characteristics(card)` (copy from `02_card_detail.py` + `06_full_pipeline.py`).

3. **`backend/src/pricepulse/scrapers/wb_feedbacks.py`** — переключить эндпоинт v1→v2, расширить `WbFeedback` dataclass полями `photo_urls: list[dict[str,str]]` и `video_urls: dict | None`, добавить CRC-16/ARC shard picker `feedbacks_host()` (copy from `_common.py`).

4. **`backend/src/pricepulse/scrapers/wb.py`** —
   - В `search()` после получения списка SKU вызывать enrichment fan-out по аналогии с Ozon (PDP + reviews параллельно через `asyncio.gather`)
   - `dest_for(region_id)` из карты `YANDEX_LR_TO_WB_DEST` вместо хардкода Москвы
   - В `_to_offer` добавить `images: [...]` (gallery) и `reviews: [...]` (top-N с photo_urls)

5. **`backend/src/pricepulse/domain/models.py`** — уже расширили под Ozon (`images: list[HttpUrl]`, `reviews: list[dict]`, `reviews_count`). Дополнительно ничего не надо.

6. **`backend/src/pricepulse/orchestrator/search.py`** — раскомментировать `SourceKind.WB: WildberriesScraper()` в `_registry` (сейчас TEMP-выключено в Ozon-only mode).

## Сводный ресерч

Полные отчёты трёх параллельных агентов сохранены в conversation log (Git history
коммитов с пометкой `feat(wb-research)`). Ключевые источники:

**WB API endpoints:**
- WB highload архитектура (basket-storage) — `habr.com/ru/companies/wildberries/articles/967988/`
- Парсинг WB (Amvera) — `habr.com/ru/companies/amvera/articles/948988/`
- WildberriesToolsMCP (Feb 2026, активный) — `github.com/Happyfunnysad/WildberriesToolsMCP`
- glmn/wb-private-api (Node, CRC-16/ARC формула) — `github.com/glmn/wb-private-api`
- Insomnia collection с полным набором параметров — `github.com/teocci/go-fiber-web/blob/main/wildberries-api-insomnia.json`
- Bug-bounty scope (подтверждает что unofficial API tolerated) — `bugbounty.standoff365.com/en-US/programs/wildberries/`

**Anti-bot:**
- "Антибот в мобильном приложении WB" (WB, 2026) — `habr.com/ru/companies/wildberries/articles/1032556/`
- WB rate-limit headers — `dev.wildberries.ru/en/docs/openapi/api-information`
- WB/Ozon/Сбер блокируют VPN — `habr.com/ru/articles/1021392/`

**Reviews / photos:**
- Live-verified `feedback-06.wbbasket.ru` (с дефисом!), HLS видео-сегменты — собственные проверки в Playwright (см. собственный код в `04_feedbacks_v2.py`)
- glmn shard formula (но устаревший photo host) — `github.com/glmn/wb-private-api/blob/main/src/WBProduct.js`
- WB OpenAPI sellers (для сверки полей) — `dev.wildberries.ru/en/docs/openapi/user-communication`

## Что НЕ нужно делать

- **Не запускай браузер для WB** — пустая трата ресурсов. Public API не требует JS-challenge.
- **Не используй curl_cffi по умолчанию** — медленнее httpx без выигрыша. Только как plan-B в коде scrapers/wb.py.
- **Не парси HTML страницы** `wildberries.ru/catalog/{nm}/detail.aspx` — там бывает поведенческий challenge. JSON-API всё это даёт.
- **Не ходи на mobile app API** — за ним PoW + Play Integrity attestation, требует реализации SDK challenge.
- **Не запускай 07 (429 probe) в проде** — это специально триггерит rate-limit.
