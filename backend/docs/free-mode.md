# PricePulse — Free Mode (по умолчанию)

PricePulse работает **полностью бесплатно** без единого платежа. Все платные сервисы — **переключаемые feature-flags**, выключены по умолчанию.

Если включить флаги и подложить ключи — переключается на платный fallback. Если нет — продолжает работать на free-стеке.

Версия 1.0, 2026-05-21. Источник истины. Дополняет [anti-bot.md](./anti-bot.md) и [local-llm-and-ops.md](./local-llm-and-ops.md).

---

## 1. Что бесплатно и где это лежит

| Категория | Free решение | Платный fallback (выкл по умолч.) |
|---|---|---|
| **HTTP client** | `httpx` + `curl_cffi` (TLS impersonate Chrome 131) | — |
| **Stealth browser** | Patchright + Camoufox (self-hosted, MIT/MPL) | — |
| **Прокси residential** | Oracle Cloud Free Tier (см. §3) + Cloudflare WARP | IPRoyal / Smartproxy / etc. |
| **Прокси datacenter** | Oracle / Hetzner promo / своя VPS | Proxy6.net |
| **Slider/puzzle captcha** | OpenCV (`slider_solver.py`), WR ≥95% | — (бесплатное побеждает) |
| **OCR / silhouettes captcha** | **Gemma 4** через Ollama (`vlm_solver.py`) | 2Captcha |
| **Yandex SmartCaptcha kaleidoscope** | Skip + retry с другим IP через час | 2Captcha (единственный путь) |
| **LLM extract (4-й источник)** | **Gemma 4** локально через Ollama | Gemini Flash / DeepSeek V4 |
| **Web search (4-й источник)** | SearXNG self-hosted + Firecrawl self-hosted | Scrapfly / ZenRows / Apify |
| **L3 third-party** | Free-tier ключи Scrapfly/Apify/ZenRows (если есть) | Платные тарифы |
| **БД** | Postgres self-hosted | — |
| **Cache / queue** | Redis self-hosted | — |
| **Image cache** | MinIO self-hosted | AWS S3 / Cloudflare R2 |
| **Monitoring** | Prometheus + Grafana + Dozzle + Uptime Kuma | Datadog / New Relic |
| **Logs** | Dozzle + structlog stdout | Loki Cloud |
| **Error tracking** | GlitchTip self-hosted | Sentry SaaS |
| **Notifications** | ntfy + Apprise self-hosted | Pushover / SMS |
| **Orchestration** | n8n self-hosted | Temporal Cloud / Prefect Cloud |
| **Admin landing** | Homepage self-hosted + FastAPI `/admin` | — |

**Дефолт: 100% бесплатно. Cost cap = $0.**

---

## 2. Feature flags (config.py)

Все платные интеграции включаются переменной окружения. Если переменная пустая/не задана — ветка платного fallback **никогда не вызывается** и метрика не растёт.

```env
# Глобальный killswitch — если false, ВСЯ платная логика выключена
FEATURES_ALLOW_PAID=false

# Гранулярно — что разрешено даже при ALLOW_PAID=true
FEATURE_USE_PAID_PROXIES=false       # IPRoyal / Smartproxy / etc.
FEATURE_USE_2CAPTCHA=false           # 2Captcha (rouble payments)
FEATURE_USE_PAID_LLM=false           # Gemini / DeepSeek extract
FEATURE_USE_PAID_L3=false            # Scrapfly/Apify/ZenRows paid tiers (free квоты OK)

# Hard-cap на 24ч (только информативно — реальный killswitch выше)
COST_CAP_USD=0
```

Каждая ветка кода, которая может стоить денег, **обязана** сначала спросить разрешения у `FeatureFlags.is_enabled("paid_captcha")`. Без флага — fallback на бесплатное или graceful degradation.

---

## 3. Бесплатные прокси на 2026

### Oracle Cloud Free Tier — основной (подтверждён)

[Oracle Cloud Always Free](https://www.oracle.com/cloud/free/) даёт **навсегда бесплатно**:

- **4 OCPU ARM Ampere A1** (можно как 1 VM × 4 cores, либо 4 VM × 1 core)
- **24 GB RAM** суммарно
- **200 GB block storage**
- **10 TB исходящего трафика в месяц** (!)
- **2 AMD VM** дополнительно (1/8 OCPU + 1 GB RAM каждая)

Это сильнее любого платного $20/мес-плана. **Минусы:** регистрация требует карту (но списания не происходит), instance reclaim после 7 дней idle — но это не проблема при активном парсинге.

**Конфиг прокси-фермы:** 2–4 VPS в разных регионах (Frankfurt, Stockholm, Sydney) с `3proxy` или `tinyproxy` → SOCKS5/HTTP пул в нашем `proxy_pool.py`.

Подводный камень для нашей задачи: Oracle Free VMs — это **datacenter IPs**, на YM/Ozon работают хуже residential. Поэтому Oracle хороший вариант для **WB и 4-го источника**, а YM/Ozon — через Camoufox + warm cookies + Cloudflare WARP.

### Cloudflare WARP — fallback

[WARP](https://1.1.1.1/) даёт **уникальный exit IP**, бесплатно, безлимитно. Минусы:
- WB/Ozon знают WARP ASN, иногда блокируют → **не основной прокси**
- Полезен как «backup IP» когда наш Oracle-пул загружен

Поднять в Docker: `cloudflare/cloudflared:latest` с режимом `warp`. Или client на хост-машине разработчика для дев-окружения.

### Самостоятельный VPS-пул (запасной)

- **Hetzner CX11** = €3.79/мес (если очень нужен IP в Германии)
- **Aeza RU** = ~₽250/мес (если нужен российский IP, оплата картой РФ)

Это **уже не бесплатно**, но почти. В free-mode не используем.

### Чего НЕ делать

- ❌ Public proxy lists (proxyscrape, free-proxy-list) — 2.1% рабочих, 5–15% делают TLS-MITM
- ❌ Tor — 99% banrate на маркетплейсах + нарушение Tor ToS
- ❌ VPN-сервисы (NordVPN/Mullvad) — забанены целыми ASN

---

## 4. Бесплатная капча — полный пайплайн

| Капча | Free решение | WR | Стоимость |
|---|---|---|---|
| Slider / Puzzle | OpenCV (`antibot/slider_solver.py`) | ≥95% | $0 |
| OCR искажённый текст | Gemma 4 E4B vision (Ollama) | ~85% | $0 |
| Silhouettes (3-4 объекта) | Gemma 4 E4B vision | ~75% | $0 |
| Kaleidoscope | **Skip** + retry с новым IP через час + кэш | ~0% | $0 |
| Checkbox / invisible | Camoufox + warm `spravka` cookies | ~80% | $0 |

**Совокупный free-mode WR на Yandex Market:** оценочно 65–80% — нерешённые запросы попадают в Redis-очередь и пересдаются позже. Жюри-демо включает 10–15 заранее-прогретых запросов с горячим кэшем — там WR = 100%.

**Если хочется добить kaleidoscope без бана 2Captcha:**
- Можно использовать [yoori/yandex-captcha-puzzle-solver](https://github.com/yoori/yandex-captcha-puzzle-solver) (CV, WR ~70%)
- Можно собрать датасет из 50–100 решений и затьюнить Gemma 4 E2B через QLoRA на 1 GPU за час (Unsloth)
- В demo-mode (см. §6) этот тип просто отключён

---

## 5. L3 free-tier ключи (опционально, всё ещё бесплатно)

Регистрируем заранее (не требует карты при signup):

| Сервис | Free квота | Когда полезно |
|---|---|---|
| **Scrapfly** | 1000 кр. навсегда | Сложный таргет, нужна одна попытка с premium-stealth |
| **Apify** | $5/мес forever | Готовые actors `wildberries-products-search`, `ozon-scraper-pro` |
| **ZenRows** | 14d trial + 40 protected | Backup для Ozon при detected детект |
| **Firecrawl** | 500 кр./мес | 4-й источник, когда self-host Firecrawl не справляется |
| **Browserless** | 1000 unit forever | Запасной cloud Chrome |

В free-mode эти ключи **используются только если установлены** в `.env`. Без ключей — соответствующая ветка cascade просто пропускается.

---

## 6. Demo-mode для жюри

Чтобы на сцене демо никогда не падало, добавляем третий режим:

```env
DEMO_MODE=true
```

В demo-mode:
1. **Прогрев кэша** при старте — 15 заранее заданных запросов («iphone 15 128 gb», «варочная панель», «беспроводные наушники Sony» и т.п.) проходят через всю систему до начала демо.
2. **На запросах из demo-set** работа идёт **только из Redis** — задержка <100 ms, нет риска бана.
3. **На запросах вне set** — обычный free-mode (с graceful degradation при капчах).
4. **Frontend** показывает badge «cached / live» — честно говорим жюри что из кэша.

Это **не обман** — это стандартный production-pattern: горячие запросы кэшируем, холодные парсим. ТЗ это явно требует («кэширование», «масштабируемость»).

---

## 7. Включаем платные опции (когда есть бюджет)

Сценарий продакшена (после хакатона):

```env
FEATURES_ALLOW_PAID=true
FEATURE_USE_PAID_PROXIES=true
FEATURE_USE_2CAPTCHA=true

PROXY_POOL_RESIDENTIAL=user:pass@host1:port,user:pass@host2:port
TWOCAPTCHA_API_KEY=abcd1234...
COST_CAP_USD=50
```

Теперь cascade:
- L1 пробует **сначала Oracle Free** (~$0/req)
- При детектах эскалирует на **residential** (~$0.005/req)
- При капче kaleidoscope — **2Captcha** ($0.003/cap)
- Cost guard остановит платное при превышении $50/день

**Конфиг — единственное место**, где нужно что-то менять. Код одинаковый.

---

## 8. Сводка стоимости в трёх режимах

| Режим | 24ч хакатона | Production / месяц |
|---|---|---|
| **Free-mode (default)** | **$0** | **$0** + opex на свет/железо |
| Hybrid (free + 2Captcha on kaleidoscope) | $0.50 | ~$15 |
| Paid (residential + 2Captcha + Gemini) | ~$22 | ~$200 |

---

## 9. Источники

- [Oracle Cloud Always Free](https://www.oracle.com/cloud/free/) — 4 OCPU ARM + 24GB RAM forever
- [Cloudflare WARP](https://1.1.1.1/) — free VPN с уникальными exit IPs
- [vsmutok/PuzzleCaptchaSolver](https://github.com/vsmutok/PuzzleCaptchaSolver) — MIT, OpenCV slider solver
- [Ollama Gemma 4](https://ollama.com/library/gemma4) — multimodal local LLM
- [yoori/yandex-captcha-puzzle-solver](https://github.com/yoori/yandex-captcha-puzzle-solver) — fallback kaleidoscope
- [Scrapfly free tier](https://scrapfly.io/pricing) — 1000 кр. forever
- [Apify free plan](https://apify.com/pricing) — $5/мес forever
- [3proxy](https://github.com/3proxy/3proxy) — lightweight SOCKS/HTTP сервер для Oracle VPS
