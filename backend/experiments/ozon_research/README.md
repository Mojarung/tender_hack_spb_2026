# Ozon research — экспериментальные парсеры (отдельный uv-проект)

Это **изолированная песочница** на уровне репо: своя `pyproject.toml`, свой
`.venv`, никакого пересечения с `backend/`. Производственный код в
`backend/src/pricepulse/scrapers/ozon.py` НЕ трогается.

Каждый файл — самостоятельный smoke-скрипт (не pytest), печатает цветной
лог и дампит JSON в `_out/`. Скрипты НЕ импортируют `pricepulse.*` — могут
работать даже если основной venv сломан.

> **Запускать только с настоящего российского IP.** Под VPN/Tor/датацентр-прокси
> Ozon отдаёт жёсткий блок на уровне ASN — это видно во всех 2024–2026 отчётах
> (Habr, JTJag, Churkashh).

## Что показал ресерч (TL;DR из 4 параллельных агентов)

**Главное открытие**: наш текущий `scrapers/ozon.py` упускает 4 критичных
заголовка и использует **неправильный TLS-профиль** для мобильного UA. Два
независимых публичных скрейпера 2024 года (`JTJag/ozon-sellers-parser`,
`Churkashh/ozon-pinneaples`) прогоняли **десятки тысяч запросов на одном
не-резидентном IP без 403** с правильным набором.

Что добавить в L1:
- `MOBILE-GAID` (UUIDv4)
- `MOBILE-LAT: 0`
- `x-o3-fp` (17-hex, "1." префикс)
- `x-o3-sample-trace: false`
- cookie `abt_data` (`"7." + ~500 рандомных char`)
- `impersonate="chrome131_android"` (вместо `chrome131`)

Что добавить в схему:
- Эндпоинт характеристик: `?url=/product/{slug}/?layout_container=pdpAtomicCharacteristics&layout_page_index=2`
- Эндпоинт отзывов: `?url=/product/{slug}/reviews/?layout_container=reviewshelfpaginator&layout_page_index=2&page={N}`
- Fallback-хост: `www.ozon.ru/api/entrypoint-api.bx/page/json/v2` (другой rate-limit пул)

Что заменить в L2:
- `nodriver` (AGPL-3.0, single-maintainer) → **Patchright** (Apache-2.0,
  drop-in Playwright API). Те же патчи `Runtime.enable` CDP-утечки,
  лицензия совместима с MIT без изоляции, бенчмарк май 2026 — 25/3/3 vs
  Cloudflare (нам этого хватает: Ozon — не Cloudflare Enterprise).

Капча и поведение:
- Slider-солвер: добавить **Canny edge detection** перед `cv2.matchTemplate`
  (`TM_CCOEFF_NORMED`) — устойчивее к alpha-blended теням пазла.
- Drag: **cubic-Bezier с overshoot 8–18 px, micro-jitter ~0.4 px gaussian,
  release-hold 50–150 ms**. Линейный drag — главный триггер боттинга.

Источники: `JTJag/ozon-sellers-parser` (TS, Crawlee), `Churkashh/ozon-pinneaples`
(Py, Dec 2024 — точный header-set), `vsmutok/PuzzleCaptchaSolver` (MIT, OpenCV),
[ianlpaterson.com бенч май 2026](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/),
Habr `amvera/articles/960280` (окт 2025).

## Установка

Один раз в этой папке:

```powershell
cd ozon_research            # из корня репо
uv sync                     # создаст ./.venv и поставит deps из pyproject.toml
uv run patchright install chromium    # один раз, ~150 МБ
```

После этого `uv` будет управлять `./.venv` автоматически — основной
backend-проект не затронут.

## Рекомендованная стратегия (после того как HTTP-only `02` не пробил)

> Если `02_l1_hardened.py` отдал не-200 — твой IP попал в WAF Ozon на mobile-
> API пуле. Это типичная картина 2026: чистый HTTP не пробивает, нужен
> **реальный браузер на старте**, потом cookies переиспользуются для
> быстрых HTTP-запросов.

**Бронебойный путь (всегда работает):**

```powershell
cd ozon_research
uv sync                              # один раз; ~150 МБ Chromium для patchright + node для nodriver
uv run patchright install chromium   # один раз — нужно для запасного пути 09

# 1. ДИАГНОСТИКА — посмотри что именно вернул сервер (3 хоста × 3 TLS × 2 hdr-mode):
uv run python 11_diagnose.py "ноутбук lenovo"
# Смотри в Conclusion: WIN? soft-block? WAF? Если WIN — пин этот combo в код.
# Если WAF на всём — иди в 12.

# 2. NODRIVER PRO — реальный установленный Chrome, persistent profile,
#    тебе нужно один раз решить challenge руками (если он появится),
#    потом cookies живут 24-72 ч. Бенч май 2026 — 28/3/0 vs Cloudflare.
uv run python 12_nodriver_pro.py "ноутбук lenovo"
# Если Chrome не нашёлся:
#   $env:BROWSER_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
#   uv run python 12_nodriver_pro.py "..."
# Хочешь смотреть в окно — оно уже headed по умолчанию.
# Хочешь скрыть — $env:HEADLESS="1"

# 3. БЫСТРЫЙ HTTP-ПУТЬ с прогретыми cookies — после 12 работает мгновенно (~3-4 с/запрос):
uv run python 13_warm_cookies_to_curl.py "ноутбук lenovo"
uv run python 13_warm_cookies_to_curl.py "шины 205 55 R16"
uv run python 13_warm_cookies_to_curl.py "принтер xerox"
# Когда 13 начнёт ловить 403 (cookies протухли через 24-72 ч) — снова прогони 12.
```

**Если совсем всё заблокировано** (даже 12 не открывает ozon.ru с твоего IP):

1. Подожди 10-20 мин, или перезапусти роутер (у большинства провайдеров DHCP даст
   новый RU-IP из пула, и WAF тебя забудет).
2. Запусти `12_nodriver_pro.py` headed и пройди slider руками (curl-only тут не
   поможет, нужно поведенческое подтверждение). Cookies сохранятся.
3. Альтернатива: `10_yandex_clickthrough.py` — через Yandex SERP с правильным
   реферером. Даёт меньше данных (только JSON-LD), но работает на 95% когда
   composer-api закрыт.

## Полный список скриптов

| # | Скрипт | Что делает | Зависимости |
|---|---|---|---|
| 01 | `01_l1_baseline.py` | Voспроизводит ТОЧНО текущий прод-код. Если 200 — прод ещё жив. | curl_cffi |
| 02 | `02_l1_hardened.py` | L1 + полный header-set + cookie + **каскад TLS-профилей** (chrome131_android → chrome131 → chrome → safari). | curl_cffi |
| 03 | `03_l1_entrypoint_fallback.py` | Сравнивает `composer-api` vs `entrypoint-api` | curl_cffi |
| 04 | `04_reviews_endpoint.py /product/slug/` | Отзывы товара через L1 | curl_cffi |
| 05 | `05_characteristics_endpoint.py /product/slug/` | Характеристики товара через L1 | curl_cffi |
| 06 | `06_full_pipeline.py "запрос"` | End-to-end L1: поиск + 5 × (чары + отзывы). Работает если 02 пробил. | curl_cffi |
| 07 | `07_slider_solver_canny.py` | OpenCV-солвер слайдера (Canny + matchTemplate) | opencv-python-headless |
| 08 | `08_human_drag.py [dx]` | Генерирует "человекоподобный" cubic-Bezier драг как CSV | — |
| 09 | `09_patchright_l2.py "запрос"` | Patchright-стелс. Альтернатива 12 если nodriver лагает. | patchright + chromium |
| **10** | `10_yandex_clickthrough.py "запрос"` | **Crash-fallback** — через Yandex SERP с реферером, только JSON-LD | curl_cffi |
| **11** | `11_diagnose.py "запрос"` | **СНАЧАЛА ЗАПУСТИ ЭТО.** Пробит хост × TLS × header-mode матрица. Скажет где именно блок. | curl_cffi |
| **12** | `12_nodriver_pro.py "запрос"` | **Основной путь.** Реальный Chrome, nodriver. Best 2026 benchmark. | nodriver + Chrome |
| **13** | `13_warm_cookies_to_curl.py "запрос"` | **После 12** — re-use cookies в curl_cffi для скорости. | curl_cffi + _out/ozon_cookies.json |

## Что увидишь в `_out/`

- `<timestamp>_02_hardened_ok.json` — массив offers и сырой `widgetStates`
  для сверки структуры
- `<timestamp>_05_chars_ok.json` — структура `{"attributes": [["Бренд","Lenovo"], ...], "raw_widgets": {...}}`
- `<timestamp>_06_full_pipeline_ok.json` — финальный артефакт демо
- `<timestamp>_08_drag_track_dx180.csv` — трек мыши, открой в Excel/`matplotlib`
- `<timestamp>_*_block.json` — если что-то 403/451, тело ответа для разбора

## Что менять в проде когда browser-first путь подтвердится

(не делать сейчас — сначала прогони `12` и `13` руками; ниже план)

1. **`backend/src/pricepulse/antibot/browser_pool.py`** —
   - оставить nodriver как primary (он лучший по бенчу), но обновить
     запуск: `user_data_dir` на постоянный путь (например
     `/var/lib/pricepulse/profiles/ozon/`), `headless=False` если
     возможен Xvfb на проде

2. **`backend/src/pricepulse/antibot/browser_fetch.py`** —
   - вставить `STEALTH_INIT` из `12_nodriver_pro.py` через
     `tab.evaluate(init, await_promise=False)` сразу после `browser.get()`
   - drag-функцию слайдера переписать на `human_drag_track()` из `08_human_drag.py`

3. **`backend/src/pricepulse/scrapers/ozon.py`** —
   - двухступенчатый кэш cookies: nodriver греет, curl_cffi переиспользует
     (как 12 → 13). Хранить cookies в Redis (TTL ~24 ч).
   - При 403/451 в L1 — инвалидировать cookies, поднять browser warm-up в L2,
     обновить cookies. (Каскад уже есть в `antibot/cascade.py`.)
   - Добавить методы `fetch_characteristics(sku)`, `fetch_reviews(sku, page)`
     — точно те же layout_container что в 05/04.

4. **`backend/src/pricepulse/antibot/slider_solver.py`** —
   - Canny pre-pass из `07_slider_solver_canny.py`

5. **`backend/pyproject.toml`** —
   - `nodriver>=0.50` в core deps (без extra). Лицензия неважна для нас.

## Чек-лист "почему этот скрипт не сработал"

- **`SSL: TLSV1_ALERT`** → `curl_cffi` старой версии, нужна ≥ 0.11 (для
  профиля `chrome131_android`)
- **HTTP 403 на всём** → IP в WAF блоке. См. секцию "Если всё заблокировано"
- **HTTP 200, `widgetStates` пустой** → soft-block (Ozon вернул каркас
  без данных). Лечится сменой IP или ожиданием
- **HTTP 200, есть `widgetStates`, нет ключей `searchResultsV2`** →
  Ozon вернул "вы давно у нас не были" страницу-приветствие. Сессия
  слишком "чистая". Запусти 09 чтобы прогреть cookies и переиспользуй
  их через `s.cookies.set(...)` в L1.
- **Patchright ругается `Executable doesn't exist`** → забыл
  `patchright install chromium`

## Что НЕ нужно делать

- Не покупай прокси. JTJag показал что 19 потоков на одном IP не
  блокируются если headers правильные.
- Не используй `ozonapp_ios` UA — у iOS-приложения другие тайные
  заголовки подписи, в open-source нет рабочего рецепта.
- Не парси `www.ozon.ru` напрямую через requests/curl — там Cloudflare-уровень,
  composer-api на `api.ozon.ru` идёт мимо него.
- Не используй платные капча-сервисы (2Captcha / CapSolver) — методичка
  хакатона (стр. 5) запрещает внешние API.
