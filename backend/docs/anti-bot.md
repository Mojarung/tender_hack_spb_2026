# PricePulse — Anti-bot / IP / Stealth-стратегия

Документ-страж: всё, что мы делаем, чтобы **не словить бан** при парсинге Wildberries, Ozon, Яндекс Маркета и плавающего 4-го источника. Сведено из 5 параллельных ресёрчей в мае 2026.

Версия 1.0, 2026-05-21.

---

## TL;DR

> **Free-mode по умолчанию.** Платные ветки в коде закрыты `FeatureFlags.allow_paid` killswitch'ом, его дефолт = `false`. Подробности — [free-mode.md](./free-mode.md).

**Cascading стратегия L1→L4** на каждый запрос. Каждый слой — отдельный circuit-breaker:

| Слой | Стоимость (free / paid) | Покрытие | Когда |
|---|---|---|---|
| **L1 — curl_cffi + Oracle Free / WARP** | **$0** / $0.001 | ~85% | По умолчанию |
| **L2 — Patchright/Camoufox + warm cookies** | **$0** / $0.005 | +10% | После 30% детектов на L1 |
| **L3 — free-tier ключи Scrapfly/Apify/ZenRows** | **$0 в квоте** | +3% | После 20% детектов на L2 |
| **L4 — OpenCV slider + Gemma 4 VLM (local)** | **$0** | +1.5% | На капчу |
| **L5 — 2Captcha (только Kaleidoscope)** | $0.003 / cap | +0.5% | Включается только `FEATURE_USE_2CAPTCHA=true` |

**Капча-стратегия (бесплатно где возможно):**

| Тип | L1 local | WR | L2 fallback |
|---|---|---|---|
| Slider / Puzzle (Ozon) | OpenCV (`antibot/slider_solver.py`) | ≥95% | 2Captcha |
| SmartCaptcha checkbox | Camoufox + warm `spravka` | ~80% | 2Captcha |
| SmartCaptcha slider | OpenCV | ≥95% | 2Captcha |
| SmartCaptcha text | Gemma 4 E4B vision (Ollama) | ~85% | 2Captcha |
| SmartCaptcha silhouettes | Gemma 4 E4B vision | ~75% | 2Captcha |
| SmartCaptcha kaleidoscope | — (skip local) | 0% | 2Captcha (только так) |

Полный документ по локальному стэку — [local-llm-and-ops.md](./local-llm-and-ops.md).

Per-source matrix:

| Источник | L1 хватает? | Главная защита | Ключевой инструмент L2 |
|---|---|---|---|
| **Wildberries** | ✅ 90%+ | rate-limit + HTTP/2 fingerprint | curl_cffi `chrome131` |
| **Ozon** | ⚠️ 60% | Cloudflare + собственный slider | Patchright + RU residential |
| **Яндекс Маркет** | ❌ 5% | SmartCaptcha + Antirobot ML | Camoufox + RU residential + warm cookies |
| **4-й (Megamarket/etc)** | зависит | разное | Crawl4AI + Patchright fallback |

---

## 1. Карта защит на 21 мая 2026

### 1.1 Wildberries

- **Собственный антибот**, не Cloudflare/DataDome. Cloudflare в РФ заблокирован с июня 2025 — это означает, что **российские IP даже выгоднее** зарубежных residential (которые ловят CF-челленджи).
- Защита агрессивна **только на mobile/seller API**: Play Integrity, нативные аттестации, PoW. На JSON-витрине (`search.wb.ru`, `card.wb.ru`, `catalog.wb.ru`, `feedbacks{1,2}.wb.ru`, `basket-XX.wbbasket.ru`) — **только rate-limit + HTTP/2 fingerprint**.
- Капча на публичных эндпоинтах **не подтверждена**.
- Triggers: 429 при >5 RPS с одного IP. Soft-bann на 30–120 с, hard-bann редкий и обычно <15 мин.

### 1.2 Ozon

- **Cloudflare на edge + собственный slider-captcha + Qrator-подобный поведенческий слой**. DataDome **НЕ обнаружен**.
- Cookies-сигналы: `cf_clearance`, `__cf_bm`, `abt_data`, `__Secure-ext_xcid`. Триггер блока — редирект на `/ozonid/sso/authenticate?code=...`.
- **Mobile-эндпоинт `api.ozon.ru/composer-api.bx/page/json/v2`** с UA `ozonapp_android/17.48.0+2528` обходит часть защиты как app-traffic. **Ключевая дёшевая дорога.**
- Triggers: 30–100 запросов с DC IP до первого детекта.

### 1.3 Яндекс Маркет

- **Yandex SmartCaptcha + внутренний Antirobot**. Срабатывает на: первый заход без `yandexuid`, агрессивный листинг, mismatch IP-региона и cookie `yp`/`ys`, странный TLS fingerprint.
- Cookies, которые надо беречь: `yandexuid` (постоянный, годы), `i`, `yp`, `ys`, **`spravka`** (золото — выдаётся после прохождения капчи, живёт 12–24 ч).
- Я.Метрика **должна быть загружена**, иначе скоринг падает.
- Triggers: ~30–60 запросов в минуту с residential RU IP при полных cookies; **1–2 запроса** с DC IP.

### 1.4 4-й «плавающий» (Megamarket / DNS / Citilink / Авито / ...)

- **Megamarket** (Сбер) — основной кандидат, мульти-категорийный, требует cookies (`mg_sid`), без капчи. Парсится `curl_cffi` с warmup.
- **DNS Shop / Citilink** — Cloudflare-light. `curl_cffi(chrome131)` обычно проходит.
- **Авито** — очень злой, нужен Patchright.

---

## 2. TLS/HTTP-fingerprinting (фундамент)

### Что фингерпринтят сервисы в 2026

- **JA3 фактически мёртв с 2023**: Chrome рандомизирует TLS extensions, JA3 стал нестабильным.
- **JA4 / JA4+** (FoxIO) — текущий стандарт. Используется Cloudflare, DataDome, Akamai, Yandex Antirobot.
- **Akamai HTTP/2 fingerprint** — порядок SETTINGS frame, WINDOW_UPDATE, header/pseudo-header order.
- **TLS+UA mismatch** = мгновенный red flag. Если User-Agent говорит «Chrome 131», а TLS-стек — питоновый `httpx` → блок.

### Python HTTP-стек (2026)

| Пакет | Версия | Зачем | Статус |
|---|---|---|---|
| **curl_cffi** | 0.15.0 (lexiforest fork) | TLS impersonate Chrome/Firefox/Safari, HTTP/2, HTTP/3 | **основной** |
| **primp** | 1.2.2 | Rust-bindings, 7/7 JA4 features vs 4/7 у curl_cffi | альтернатива |
| **httpx[http2]** | 0.28+ | для эндпоинтов без anti-bot (`search.wb.ru`) | вспомогательный |
| **tls-client** (bogdanfinn) | — | **deprecated в 2025** | ❌ не брать |
| **hrequests** | — | **deprecated** | ❌ не брать |

### Stealth-браузеры (2026)

| Пакет | Версия | Двигатель | Когда |
|---|---|---|---|
| **Patchright** | 1.60.0 | Chromium, CDP-stealth | Ozon, generic Chromium |
| **Camoufox** | 150.0.2 | Firefox + C++ stealth + BrowserForge | **Яндекс Маркет**, тяжёлый anti-bot |
| **nodriver** | 0.50.x | Chromium, преемник undetected-chromedriver | альтернатива Patchright |
| **playwright-stealth** | — | **мёртв с 2024** | ❌ |
| **undetected-chromedriver** | — | **deprecated** | ❌ |

**Бенчмарки 2026** (independent, на 31 anti-bot таргет):

| Браузер | Cloudflare | DataDome | Akamai | Yandex SmartCaptcha |
|---|---|---|---|---|
| **Camoufox** | ✅ отличный | ✅✅ ~95% | ✅ хороший | ✅✅ лучший |
| **Patchright** | ✅✅ лучший | ✅ ~67% headless | ✅ хороший | ⚠️ средний |
| **nodriver** | ✅✅ 100% | ⚠️ ~25% без прокси | ⚠️ | ⚠️ |

### Pin-list для `pyproject.toml`

```toml
"curl-cffi>=0.15.0",
"primp>=1.2.2",                  # альтернатива
"patchright>=1.60.0",
"camoufox[geoip]>=0.4.11",
"nodriver>=0.50.0",              # на случай если Patchright заблокируют
"HumanCursor>=1.1.5",            # mouse bezier для критичных сценариев
```

---

## 3. Прокси / IP стратегия

### Главные выводы

1. **Datacenter IPs мертвы для Ozon и YM** (40–60% banrate). Для WB DC ещё терпит при умеренном RPS.
2. **IPv6 бесполезен** — все три маркетплейса работают только на IPv4.
3. **VPN-сервисы (NordVPN/Mullvad/...) явно блокируются** WB и Ozon с 2025 — целые ASN на стоп-листах.
4. **Tor — 99% banrate** + нарушает Tor ToS. Не использовать.
5. **Public proxy lists (proxyscrape/free-proxy-list)** — 2.1% рабочих, 5–15% делают TLS-MITM. **Не использовать.**
6. **Hetzner / OVH / DigitalOcean** — pre-blocked по ASN на Ozon и YM. Не брать.

### Sticky session — параметры

| Источник | Sticky session длительность | Concurrency на IP | RPS на IP |
|---|---|---|---|
| **Wildberries** | 2–5 мин | 1–3 | до 5 |
| **Ozon** | 5–10 мин | 1–2 | до 1 |
| **Я.Маркет** | 10–15 мин | 1 | до 0.5 |
| **4-й (Megamarket)** | 5–10 мин | 1–2 | до 2 |

### Минимальный сетап на хакатон ($20–35)

- **IPRoyal Royal Residential**: 3 GB RU residential, **трафик не сгорает** ($20)
- **2 × Aeza VPS RU** (1 vCPU, 1 GB): $4/мес каждая, бэкап-DC IPs для WB и smoke-tests ($8 total)

**Live-demo secret weapon:** USB-tethering с обычной SIM (МТС/Yota) — даёт настоящий mobile CGNAT IP **бесплатно**. На демо открываем `iphone tether`, делаем драматичный live-запрос на сцене.

### RU residential провайдеры (май 2026)

| Провайдер | Цена / GB | RU pool | Sticky | Платёж из РФ |
|---|---|---|---|---|
| **IPRoyal Royal Residential** | $7 (start) → $1.75 (volume) | да, city-level | ✅ | карта/крипта |
| **Astroproxy** | $4–6 | да | ✅ | рубли |
| **Proxy6.net** | DC по $0.5/IP | да (DC) | ✅ | рубли |
| **Belurk** | $5 | да | ✅ | рубли |
| **SOAX** | $6–8 | да | ✅ | карта |

**Не рекомендуем:** Bright Data (зарубежная карта), Oxylabs (KYC issues для РФ).

### Free / нулевой бюджет

- **WARP / Cloudflare WARP** — даёт уникальный exit IP, но WB/Ozon палят WARP-ASN.
- **Hugging Face Spaces** / Google Colab — выходят с публичных DC, забанят.
- **Своя сеть на Aeza** — 1–2 VPS бэкапом, не основа.
- **«Кэш-first» режим**: 80% демо-запросов отвечает из Redis-кэша → потребление IP падает на порядок.

---

## 4. CAPTCHA стратегия

### Yandex SmartCaptcha (главная)

| Solver | Метод | Цена/1000 (май 2026) | Скорость | WR | Платёж из РФ |
|---|---|---|---|---|---|
| **2Captcha** | `yandexSmart` | $2.99 | 15–40 с | 92–97% (checkbox/slider) | **рубли, QIWI** |
| **CapMonster Cloud** | `YandexSmartTask` | $0.7–1.5 | 10–20 с | ниже 2captcha | крипта |
| **CapSolver** | `YandexSmartCaptchaTaskProxyless` | $1.6–3.0 | 5–15 с | AI-first | крипта/USDT |
| **Anti-Captcha** | `YandexSmartCaptchaTaskProxyless` | $2–3 | 20–40 с | 92–95% | карта |

**Рекомендация:** **2Captcha** (платежи в рублях через QIWI/тинькофф = доступно без юридических заморочек) как primary. **CapMonster** как fallback.

**Бюджет на хакатон:** на 1000 listing-запросов YM при WR=80% (Camoufox с warm cookies) — 200 капч × $0.003 = **$0.60**. На сутки демо — **<$5**.

### Self-hosted OSS-солверы

- `yoori/yandex-captcha-puzzle-solver` — только slider/puzzle, WR ~70%. Полезен как L4-секонд.
- **Писать свой ML-солвер на 24ч хакатоне нерентабельно.**

### Ozon slider

- **НЕ DataDome формат** — 2captcha DataDome solver не работает.
- CapSolver `AntiSliderTask` ~$1.5/1000 (может не покрыть конкретно Ozon slider).
- **Лучше**: Patchright + bezier mouse movement → ручной drag через `HumanCursor`. Тратит CPU, не деньги.

### Wildberries

- Капча на публичных эндпоинтах не подтверждена. **Не нужна.**

---

## 5. Per-source detailed

### 5.1 Wildberries

**Public endpoints (живы на 2026):**
```
GET https://search.wb.ru/exactmatch/ru/common/v18/search
    ?appType=1&curr=rub&dest=-1257786&lang=ru&page=1
    &query={encoded}&resultset=catalog&sort=popular&spp=30
    &suppressSpellcheck=false

GET https://catalog.wb.ru/catalog/{shard}/v2/catalog?...
GET https://card.wb.ru/cards/v2/detail?nm={id1;id2;...}
GET https://feedbacks{1,2}.wb.ru/feedbacks/v1/{imt_id}
GET https://wbx-content-v2.wbstatic.net/price-history/{nm_id}.json
GET https://basket-{XX}.wbbasket.ru/vol{nm//100000}/part{nm//1000}/{nm}/images/big/1.webp
```

**Headers (минимум):**
```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
            (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36
Accept: */*
Accept-Language: ru-RU,ru;q=0.9,en;q=0.8
Origin: https://www.wildberries.ru
Referer: https://www.wildberries.ru/
Sec-Fetch-Dest: empty
Sec-Fetch-Mode: cors
Sec-Fetch-Site: cross-site
```

**Стратегия:**
1. `httpx.AsyncClient(http2=True)` без прокси — для 80% запросов.
2. При 429 → `curl_cffi.requests.get(impersonate="chrome131")` + RU DC proxy.
3. При полном фейле → Patchright (редко).
4. **Цены — золото:** `wbx-content-v2.wbstatic.net/price-history/{nm}.json` даёт **полную многолетнюю историю** в копейках. Это ключевой demo-актив.
5. **Basket-shard алгоритм** для картинок — см. `scrapers/wb_basket.py`.

**Rate-limit:** ≤5 RPS на IP с jitter 100–300 ms. Параллельность ≤3 на эндпоинт.

### 5.2 Ozon

**Public endpoints:**
```
GET https://api.ozon.ru/composer-api.bx/page/json/v2?url=/search/?text={enc}
GET https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=...
```

**Mobile headers (ключевой обход):**
```http
User-Agent: ozonapp_android/17.48.0+2528
x-o3-app-name: ozonapp_android
x-o3-app-version: 17.48.0
x-o3-device-type: mobile
Accept: application/json; charset=utf-8
Accept-Language: ru
```

**Структура ответа:**
- `widgetStates[<key>]` — JSON-строки, **нужен второй `json.loads`**.
- Ищем ключи: `searchResultsV2-*`, `tileGridDesktop-*` (поиск), `webSale-*` (детали), `webListReviews-*` (отзывы).
- Из `mainState[].atom.text` / `atom.textRenderer.text` достаём title и price.

**Стратегия (cascading):**
```
L1: curl_cffi(chrome124) + mobile UA + x-o3-* headers
        ↓ при 403 / редирект на /ozonid/sso/authenticate / пустой widgetStates
L2: curl_cffi + RU residential proxy + ротация UA (sticky 5–10 мин)
        ↓ при повторном фейле
L3: Patchright (Chromium headed, BrowserForge fingerprint) на www.ozon.ru
    → пройти JS-челлендж → перехватить XHR к composer-api.bx
        ↓ при появлении slider-капчи
L4: HumanCursor mouse drag + Patchright (CapSolver AntiSliderTask как backup)
```

### 5.3 Яндекс Маркет

**Стратегия:**
1. **НЕ ходим напрямую через httpx/curl_cffi** — Antirobot ловит почти всё.
2. **Camoufox + geoip=True** (Camoufox автоматически выставит `ru-RU`, `Europe/Moscow`, WebRTC, шрифты).
3. **Warm storageState**: один раз вручную проходим капчу в обычном Firefox → экспортим cookies → загружаем в Camoufox через `storage_state=...`. **`spravka` живёт 12–24ч** — обновляем раз в сутки.
4. **Прогрев сессии**: заход на `yandex.ru` → `market.yandex.ru` (главная) → 3–5 сек с движением мыши → `/search?text=...`.
5. **Параллелизм**: 2–3 одновременных сессии на 1 IP. Пул 5–10 sticky-IP.
6. **Тайминги**: 2–5 с между запросами с jitter, имитация скролла (viewport-scroll обязателен — иначе lazy-load не сработает).

**Селекторы (data-атрибуты стабильны, CSS-классы хешированы):**

| Поле | Селектор |
|---|---|
| Карточка | `article[data-zone-name="snippet-card"]` |
| Заголовок | `[data-zone-name="title"] span` |
| Цена | `[data-auto="snippet-price-current"] span` |
| Old price | `[data-auto="snippet-price-old"]` |
| URL | `a[data-auto="snippet-link"]` |
| Картинка | `img[data-auto="picture-product"]` |
| Рейтинг | `[data-auto="snippet-rating"] [data-auto="rating-value"]` |
| Отзывы count | `[data-auto="reviews-count"]` |

**Стратегия парсинга:** **сначала JSON-LD** (`script[type="application/ld+json"]`) — он отдаёт цену числом и не зависит от перерисовок UI. Fallback — data-атрибуты.

**Капча fallback:** при детекте `iframe[src*="smartcaptcha"]`:
```python
sitekey = await iframe.locator("[data-sitekey]").get_attribute("data-sitekey")
token = await two_captcha.solve_yandex(sitekey, page.url)
await page.evaluate(f"window.smartCaptcha.execute({token!r})")
```

### 5.4 4-й «плавающий» источник

**Стратегия:** **Crawl4AI** + **Patchright fallback** + **LLM-extraction** через Gemini 3 Flash или DeepSeek V4 Flash.

**Поиск кандидатов:** SearXNG self-hosted → топ-10 результатов → исключаем `wildberries.ru | ozon.ru | market.yandex.ru` → передаём в Crawl4AI.

**Default candidates** (если SearXNG не работает):
- **Megamarket** (Сбер) — мульти-категорийный, парсер `xob0t/mmparser` свежий
- **DNS Shop** — электроника
- **Citilink** — электроника/IT

**Extraction schema (Pydantic):**
```python
class GenericOffer(BaseModel):
    name: str
    price: Decimal
    currency: str = "RUB"
    image: str | None
    url: str
    characteristics: dict[str, str] = {}
```

LLM-extract через **Gemini 3 Flash** ($0.075/1M input) — 100k страниц × 2k токенов = $15. Альтернатива: **DeepSeek V4 Flash** ($0.14/1M, доступнее из РФ).

---

## 6. Аналитика для жюри

### 6.1 Что вытащить публично

| Источник | Отзывы | История цен |
|---|---|---|
| **Wildberries** | `feedbacks{1,2}.wb.ru/feedbacks/v1/{imt_id}` + `public-feedbacks.wildberries.ru/api/v1/feedbacks/site` | **`wbx-content-v2.wbstatic.net/price-history/{nm}.json`** — многолетняя |
| **Ozon** | `widgetStates['webListReviews-...']` через composer-api | Только наш сбор + редко `widgetStates['webPriceHistory-...']` |
| **Я.Маркет** | первые 10 из `__NEXT_DATA__` без капчи | Нет публично; собираем сами |
| **Megamarket** | парсится из HTML | Свой сбор |

### 6.2 Топ-7 фич для жюри

1. **WB price-history spark-line** — реализуется за 30 минут, многолетняя кривая в карточке.
2. **Boxplot цен по источникам** — distribution + outliers + median (Recharts).
3. **Best-Deal Score**: `w1*price_z + w2*rating + w3*log(reviews) + w4*seller_trust − w5*delivery_days`. Дать слайдеры весов в UI.
4. **Sentiment-анализ отзывов** через `seara/rubert-tiny2-russian-sentiment` (3 ms/текст на CPU, помещается на хакатонную машину). Badge positive/neutral/negative + общий sentiment.
5. **Trust score источника** — функция от (parse_success_rate, captcha_rate, last_seen_freshness). Светофор-индикатор у логотипа маркетплейса.
6. **Word cloud по pros/cons** из WB feedbacks (поля уже структурированы).
7. **Funnel-анимация** в UI: запрос → 4 параллельных адаптера → агрегация → top-1. Демо-killer.

### 6.3 Sentiment-модели (для русского, hackathon-friendly)

| Модель | Размер | Скорость на CPU | Качество |
|---|---|---|---|
| **seara/rubert-tiny2-russian-sentiment** | 12M | **3 ms/text** | хорошее, 3 класса | **рекомендуем** |
| `blanchefort/rubert-base-cased-sentiment` | 180M | 30 ms/text | лучше | если есть RAM |
| `sismetanin/rubert-ru-sentiment-rureviews` | 180M | 30 ms/text | best для product reviews | если приоритет качество |
| DeepPavlov | разное | медленно | конфликты с Python 3.13 | ❌ не брать |

### 6.4 Frontend для analytics

- **React + Recharts** — основной выбор, покрывает 95% графиков.
- **Apache ECharts** — для тяжёлых dataset (>1k точек, например полная история цены).
- **react-d3-cloud** — word cloud.
- **react-simple-maps** — карта «город ↔ цена».
- **Не брать:** Superset (8 контейнеров), Metabase (нельзя кастом spark-line в карточке), Grafana (для метрик, не для product UI).

---

## 7. Observability

### 7.1 Prometheus + Grafana

**Стек:**
- `prometheus-fastapi-instrumentator==7.1.0` — RED-метрики FastAPI из коробки
- `prometheus-client==0.22+` — кастомные Counter/Histogram/Gauge
- `arq-prometheus` — метрики очереди arq (опц.)
- `prom/prometheus:v3.0.1`, `grafana/grafana:11.4.0`
- `node-exporter` + `cadvisor` — система и контейнеры

**Метрики на скрейпер (per-source):**
```python
scrape_requests_total{source, outcome, proxy_tier}  # Counter
scrape_duration_seconds{source}                      # Histogram (0.1, 0.5, 1, 2, 5, 10, 20, 30, 60)
scrape_offers_returned_total{source}                 # Counter
proxy_in_use{tier}                                   # Gauge
captcha_solve_attempts_total{source, provider, outcome}  # Counter
cache_hits_total{source}, cache_misses_total{source}     # Counter
browser_pool_size{source, status="idle|busy"}        # Gauge
arq_queue_length{queue}                              # Gauge
scrape_cost_units_total{source, cost_type}           # Counter (µUSD)
```

**Cardinality**: `source × outcome × proxy_tier = 4×6×4 = 96` серий — комфортно. **НЕ** класть `proxy_ip`, `product_id`, `url` в labels.

**Dashboards** на grafana.com:
- **16110** — FastAPI Observability (адаптируем под наши `http_*`)
- **1860** — Node Exporter Full
- **14282** — cAdvisor exporter
- Свой **«Live Scraping»** — см. `monitoring/grafana/dashboards/live-scraping.json`.

### 7.2 n8n как visibility layer

**3 workflow на хакатон:**

1. **Smoke test all sources** — Schedule (`*/5 * * * *`) → 4 параллельных HTTP-вызова к `api:8000/scrape/{source}` → Telegram alert при ошибке.
2. **Captcha alert + auto-fallback** — Webhook от FastAPI при >5 капч подряд → переключение адаптера в более дорогой режим через `POST /admin/fallback/{source}` → Telegram log.
3. **Live demo board** — Webhook вход → 4 параллельных HTTP → Merge → анимированный canvas для демо.

**Storage:** Postgres backend (отдельная схема `n8n`), workflow JSON в git как `n8n/workflows/*.json`.

**MCP integration:** [`czlonkowski/n8n-mcp`](https://github.com/czlonkowski/n8n-mcp) — генерирует workflow из текста в Claude Code за минуты.

### 7.3 S3-совместимое хранилище (MinIO)

**Зачем:** кэш картинок товаров, чтобы не дёргать WB basket-CDN / Ozon image-CDN на каждый показ.

**Стек:**
- `minio/minio:latest` в docker-compose (один контейнер, S3 API на :9000, console на :9001).
- `boto3>=1.36` или `aioboto3>=13` в Python.
- Bucket `pricepulse-images`, lifecycle policy: TTL 30 дней.

**Flow:**
1. Скрейпер собрал `image_url` от источника.
2. Async download → upload в MinIO как `images/{source}/{hash}.webp`.
3. В БД хранится **наш** URL (`http://minio:9000/pricepulse-images/...`), а не источника.
4. Преимущества: устойчивость к ребрендингу basket-shard у WB, унификация формата, можно отдавать через CDN.

---

## 8. Финальный docker-compose layout

```yaml
services:
  api:                  # FastAPI + Patchright + Camoufox
  worker:               # arq, тяжёлые scrape-задачи
  postgres:             # OLTP + n8n backend
  redis:                # cache + arq queue + rate-limit
  firecrawl-api:        # 4-й источник, fallback
  searxng:              # бесплатный мета-поиск
  minio:                # S3 для картинок
  prometheus:           # метрики
  grafana:              # дашборды
  node-exporter:        # system metrics
  cadvisor:             # container metrics
  n8n:                  # orchestration + observability + alerts
```

См. конкретный compose в `backend/docker-compose.yml`.

---

## 9. План на 24 часа хакатона (priority-ordered)

**P0 (must, 0–8 ч):**
- [ ] L1-стек: `curl_cffi` + `httpx` для WB, Ozon mobile, Megamarket
- [ ] WB price-history endpoint в demo
- [ ] WB feedbacks endpoint + базовый sentiment
- [ ] SSE streaming результатов в UI
- [ ] Prometheus instrumentator + 4 базовые метрики
- [ ] docker-compose up даёт работающий API

**P1 (should, 8–18 ч):**
- [ ] Patchright для Ozon detail pages
- [ ] Camoufox + warm cookies для YM
- [ ] 2Captcha integration для YM (когда сработает)
- [ ] Crawl4AI для 4-го источника
- [ ] Grafana dashboard «Live Scraping» — 6 рядов
- [ ] n8n + workflow «Smoke test»
- [ ] MinIO + image caching

**P2 (nice-to-have, 18–24 ч):**
- [ ] Best-Deal Score с UI-слайдерами весов
- [ ] Boxplot + word cloud
- [ ] Карта «город ↔ цена»
- [ ] n8n workflow «Captcha auto-fallback»
- [ ] Funnel-анимация демо
- [ ] BPMN в `docs/bpmn.svg`

**Чего избегать (anti-patterns):**
- ❌ OpenTelemetry traces (overkill за 24ч)
- ❌ Bright Data (зарубежная карта)
- ❌ Tor / public proxy lists / VPN-сервисы
- ❌ Своя ML-капча (нерентабельно)
- ❌ Local Ollama для extraction (если нет GPU)
- ❌ Superset / Metabase (BI overkill)
- ❌ Apache Airflow / Temporal (orchestration overkill, у нас есть arq)

---

## 10. Cost guard

Hard-cap бюджета на 24ч хакатона: **$50**.

| Статья | Стоимость | Hard-cap | Контроль |
|---|---|---|---|
| Прокси (IPRoyal RU residential, 3GB non-expiring) | $20 | $25 | trafic-counter |
| 2Captcha | ~$3–5 | $10 | balance-poll каждые 5 мин |
| LLM-extract (Gemini Flash) | ~$3 | $10 | token-counter в logs |
| VPS (Aeza ×2, бэкап IPs) | $1.5 | $5 | месяц-prepaid |
| **Итого** | **~$28** | **$50** | |

**Auto-disable при превышении hard-cap:** при `sum(scrape_cost_units_total) > 50_000_000` → Telegram алерт → автоматическое отключение платных слоёв L3/L4, остаёмся на L1/L2 + cache.

---

## 11. Источники

### Anti-bot tools

- [pim97/anti-detect-browser-tools-tech-comparison](https://github.com/pim97/anti-detect-browser-tools-tech-comparison) — главное сравнение 2026
- [Patchright (Kaliiiiiiiiii-Vinyzu)](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
- [Camoufox (daijro)](https://github.com/daijro/camoufox), [Stealth docs](https://camoufox.com/stealth/)
- [nodriver (ultrafunkamsterdam)](https://github.com/ultrafunkamsterdam/nodriver)
- [curl_cffi (lexiforest)](https://github.com/lexiforest/curl_cffi)
- [primp (deedy5)](https://github.com/deedy5/primp)
- [Crawl4AI (unclecode)](https://github.com/unclecode/crawl4ai)

### Per-source

- [Хабр: search.wb.ru v18 (Amvera, 2025)](https://habr.com/ru/companies/amvera/articles/948988/)
- [Хабр: антибот WB официально](https://habr.com/ru/companies/wildberries/articles/1032556/)
- [Apify Wildberries Products Search Scraper](https://apify.com/powerai/wildberries-products-search-scraper/api)
- [Duff89/wildberries_parser](https://github.com/Duff89/wildberries_parser) — basket shard алгоритм
- [Хабр: парсим Ozon (Amvera, 2025)](https://habr.com/ru/companies/amvera/articles/960280/)
- [Churkashh/ozon-pinneaples](https://github.com/Churkashh/ozon-pinneaples) — Ozon mobile endpoint
- [DxDiagDx OZON parser gist](https://gist.github.com/DxDiagDx/710ac65e117bdd45d4dbb3c64d07849c)
- [Yandex SmartCaptcha tasks](https://yandex.cloud/en/docs/smartcaptcha/concepts/tasks)
- [xob0t/mmparser (Megamarket)](https://github.com/xob0t/mmparser)

### CAPTCHA

- [2Captcha — Yandex SmartCaptcha](https://2captcha.com/p/yandexsmart) — принимает рубли
- [CapMonster Cloud — Yandex](https://capmonster.cloud/)
- [CapSolver pricing](https://docs.capsolver.com/en/pricing/)

### Anti-bot services (foreign)

- [Bright Data Web Unlocker](https://brightdata.com/pricing/web-unlocker)
- [Scrapfly pricing](https://scrapfly.io/pricing) — 1000 кредитов навсегда
- [ZenRows pricing](https://www.zenrows.com/pricing) — 14d trial + 40 protected
- [Apify pricing](https://apify.com/pricing) — $5/мес forever
- [Apify — Ozon Scraper PRO](https://apify.com/zen-studio/ozon-scraper-pro/api)
- [Apify — Yandex Market Scraper](https://apify.com/zen-studio/yandex-market-scraper-parser/api)

### Sentiment

- [seara/rubert-tiny2-russian-sentiment](https://huggingface.co/seara/rubert-tiny2-russian-sentiment) — рекомендован
- [blanchefort/rubert-base-cased-sentiment](https://huggingface.co/blanchefort/rubert-base-cased-sentiment)

### Observability

- [trallnag/prometheus-fastapi-instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator)
- [Grafana 16110 — FastAPI Observability](https://grafana.com/grafana/dashboards/16110-fastapi-observability/)
- [n8n Docker docs](https://docs.n8n.io/hosting/installation/docker/)
- [czlonkowski/n8n-mcp](https://github.com/czlonkowski/n8n-mcp)
- [MinIO Docker](https://min.io/docs/minio/container/operations/installation.html)

### Прокси

- [IPRoyal Royal Residential](https://iproyal.com/residential-proxies/)
- [Proxy6.net](https://proxy6.net/)
- [Astroproxy](https://astroproxy.com/)
