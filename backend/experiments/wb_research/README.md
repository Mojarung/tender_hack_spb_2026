# Wildberries research — проверка anti-bot техник из Ozon

Это изолированная песочница для проверки, можно ли подходы из
`ozon_research/` применить к Wildberries.

## Вывод на старте

Ozon-специфичные части напрямую переносить в WB нельзя:

- `ozonapp_android` UA, `x-o3-*`, `MOBILE-GAID`, `abt_data` — это внутренний
  протокол Ozon, для WB бесполезен и может выглядеть подозрительно.
- Ozon composer-api и same-origin fetch через `www.ozon.ru/api/...` не имеют
  аналога 1-в-1 у WB.

Что можно переиспользовать:

- `curl_cffi` TLS impersonation вместо обычного `httpx`, если WB начнёт давать
  403/429/пустые ответы.
- Аккуратный header-set браузерного CORS-запроса: `Origin`, `Referer`,
  `Sec-Fetch-*`, `Accept-Language`.
- Cascade L1 → L2: сначала публичный JSON endpoint, потом browser warm-up и
  same-origin/cross-origin fetch из настоящего браузера.
- Rate limit + retry/backoff. Для WB это важнее всего, потому что основной риск
  сейчас — 429, а не captcha.

## Что проверяем

1. `01_httpx_baseline.py` — текущий production-путь: `httpx` → `search.wb.ru`.
2. `02_curl_cffi_impersonate.py` — тот же endpoint, но через `curl_cffi` с
   TLS impersonation и теми же browser headers.
3. `03_plain_request.py` — минимальный request shape без `Origin`, `Referer`,
   `Sec-Fetch-*`, близкий к системному `curl`.
4. `04_safe_rate_probe.py` — безопасный замер устойчивого темпа: последовательные
   запросы, паузы с jitter, остановка на `403/429`, фиксация `Retry-After`.

Если `01` стабильно работает, production лучше не усложнять. Если `01` ловит
429/403, а `02` проходит — можно переносить `curl_cffi` fallback в
`backend/src/pricepulse/scrapers/wb.py`.

## Первый локальный прогон

На текущем IP оба варианта получили `HTTP 429 Too Many Requests`:

- `01_httpx_baseline.py "iphone 15"` → 429;
- `02_curl_cffi_impersonate.py "iphone 15"` → 429 на `chrome131`, `chrome`,
  `safari17_2_ios`.

Это важный результат: для WB проблема сейчас не чинится простой заменой
`httpx` на `curl_cffi`.

Дополнительная ручная проверка через системный `curl` показала интересную
деталь:

- `curl` без browser headers получил `200 OK` и JSON;
- в ответе был заголовок `x-pow: status=invalid;challenge=...`;
- `curl` с browser-like `Origin/Referer/Sec-Fetch-*` получил `429`.
- `03_plain_request.py "iphone 15"` тоже получил `200 OK`, `products=100`,
  при этом WB вернул `x-pow: status=invalid;challenge=...`.

То есть у WB включён WBAAS/Proof-of-Work слой. Для production надо не
копировать Ozon headers, а разобраться с WB `x-pow` challenge или подобрать
запрос, который стабильно проходит без browser-CORS сигнатуры.

Практический быстрый фикс для production: убрать из `wb.py` CORS/browser
headers (`Origin`, `Referer`, `Sec-Fetch-*`) и оставить plain request shape.
Это не решает `x-pow` окончательно, но на текущем IP уже превращает `429` в
валидный JSON.

Следующие кандидаты на проверку:

1. Убрать лишние browser headers из production `wb.py` и проверить plain
   request shape через `03_plain_request.py`.
2. Замерить безопасный бюджет через `04_safe_rate_probe.py` и выставить
   production `WB_RPM` ниже первого проблемного шага минимум на 30–50%.
3. Проверить альтернативные WB shard/endpoint URLs только как совместимые
   публичные endpoint-ы, без ротации идентичности и обхода блокировок.
4. Только потом делать L2 browser warm-up; Ozon mobile headers (`x-o3-*`,
   `abt_data`) к WB не применимы.

## Безопасная стратегия без банов

- Соблюдать один общий бюджет запросов на WB через Redis-backed `RateLimiter`,
  чтобы несколько воркеров не складывали нагрузку.
- Для WB начинать консервативно: `6–10 RPM` на весь сервис, повышать только
  после живого замера, а не по параллельным нагрузочным тестам.
- При `429` или `403` немедленно останавливать источник, уважать `Retry-After`,
  если он есть, иначе делать cooldown не меньше `120s`.
- Добавлять jitter к паузам, чтобы не создавать ровный метроном запросов.
- Агрессивно использовать кэш поисковых ответов и дедупликацию одинаковых
  запросов на уровне оркестратора.
- Не переносить browser/CORS headers в WB, если plain request стабильно даёт
  JSON: текущий замер показал, что browser-like shape может ухудшать ситуацию.
- Не использовать ротацию IP, подмену идентичности или обход challenge-слоёв:
  это повышает риск блокировок и выходит за рамки устойчивой интеграции.

## Прогон `04_safe_rate_probe`

Запуск `04_safe_rate_probe.py "iphone 15"` на текущем IP:

- первый запрос уже на минимальном шаге `6 RPM` получил `HTTP 429`;
- `Retry-After` отсутствовал;
- рекомендованный cooldown по протоколу эксперимента — `120s`;
- вывод: текущий IP/сигнатура уже находится в ограничении или WB блокирует
  даже одиночный plain-запрос, поэтому повышать RPM нельзя.

Практический вывод для production: при таком состоянии источник WB должен
быстро деградировать в пустой результат/кэш, не ретраить агрессивно и не
создавать очередь повторных запросов.

## Раунд 2 — поиск устойчивых production-приёмов

Раунд 1 показал, что простая замена `httpx → curl_cffi` ничего не решает,
и любой browser-CORS shape ухудшает ситуацию. Поэтому раунд 2 искал то,
что можно безопасно использовать в проде **без ротации IP, без подмены
идентичности и без обхода challenge**.

Скрипты:

- `05_xpow_inspect.py` — серия plain-запросов, дамп переходов `x-pow` и
  cookie-jar.
- `06_endpoint_shards.py` — sweep по альтернативным WB host-ам:
  `search.wb.ru`, `u-search.wb.ru`, `search-by-regions.wb.ru`,
  `search.wb.ru/v17`, `suggestions.wildberries.ru`.
- `06b_usearch_deep.py` — структурный анализ ответа `u-search.wb.ru`.
- `07_cookie_warmup.py` — GET `https://www.wildberries.ru/` → search с
  общей cookie-jar.
- `08_recovery_time.py` — реальное время восстановления после 429.
- `09_keepalive_session.py` — keepalive client vs новый client на каждый
  запрос.
- `10_apptype_variants.py` — sweep `appType=1/64/128`.

### Ключевые наблюдения

1. **`u-search.wb.ru` отдаёт полный v18 ответ, когда `search.wb.ru` уже 429.**
   Проверено дважды с разрывом 5 минут, с одного и того же IP. Структура
   тела идентична prod-парсеру (`products[]` на top-level, 100 элементов,
   те же ключи `id/brand/sizes/...`). Это полностью совместимый
   публичный fallback shard — в отличие от ротации IP/идентичности он
   не противоречит методичке.
2. **`search-by-regions.wb.ru` и `suggestions.wildberries.ru` — таймауты**
   (10 с). С продакшна толку нет.
3. **`v17` ведёт себя как `v18` — те же 429.** Не fallback.
4. **`appType=1/64/128` — все 429 одновременно.** Лимит не разделён по
   `appType`, переключать бессмысленно.
5. **`keepalive` vs новый client per request — статистически идентично**
   (6/6 = 429 в обоих режимах). Гипотеза «новый TLS handshake = больше
   429» не подтвердилась. Не нужно усложнять prod-сценарий.
6. **`07_cookie_warmup`: главная WB вернула HTTP 498 без cookies**, но
   следующий `search.wb.ru` сразу прошёл (200). Это значит, что cookies
   не нужны для прохождения; PoW-слой пускает первый запрос plain-shape
   и только после второго даёт 429. Бюджет — ~1 запрос на короткое окно.
7. **Recovery после 429 — секунды, не минуты.** `08_recovery_time` дал
   200 на первом probe через ~10 с после серии 429. Production-cooldown
   `120 s` сильно завышен; разумнее 15–30 с со step-up при повторе.
8. **`x-pow: status=invalid` присутствует на 200-ответах.** То есть WB
   не блокирует «invalid» состояние, он использует его как сигнал. Мы
   не должны пытаться его «решать».

### Production-рекомендации (без рисков)

- **P0** — добавить `u-search.wb.ru` как second-chance endpoint в
  `scrapers/wb.py`. Логика: `search.wb.ru` → если 429/таймаут → один
  retry на `u-search.wb.ru` с теми же params. Это публичный shard
  того же сервиса, не «обход».
- **P0** — сократить `_COOLDOWN_S` с `120` до `30` и сделать его
  step-up: `30 → 60 → 120 → 180`, увеличение при повторных 429 в
  пределах 5 минут, сброс после успешного ответа.
- **P0** — оставить plain headers (как уже сделано в `wb.py` после
  раунда 1), не добавлять `Origin/Referer/Sec-Fetch-*`.
- **P1** — продолжать держать `wb_rpm ≤ 6` и singleflight-дедупликацию
  на оркестраторе (уже есть). Эксперимент 09 показал, что connection
  reuse сам по себе ничего не даёт — то есть persistent client можно
  не вводить.
- **NOT** — не добавлять homepage warm-up: `498` показывает, что для
  нашего IP-класса HTML-заход активно ограничен, а cookies всё равно
  не помогают. Лишний шаг, который не даёт уменьшения 429.
- **NOT** — не переключать `appType`, не пробовать v17, не делать
  retry с `curl_cffi`: ни один из трёх не отличается от baseline.

## Запуск

```powershell
cd C:\proga\tender_hack_spb_2026\backend\experiments\wb_research
uv sync
uv run python 01_httpx_baseline.py "iphone 15"
uv run python 02_curl_cffi_impersonate.py "iphone 15"
uv run python 03_plain_request.py "iphone 15"
uv run python 04_safe_rate_probe.py "iphone 15"
uv run python 05_xpow_inspect.py "iphone 15"
uv run python 06_endpoint_shards.py "iphone 15"
uv run python 06b_usearch_deep.py "iphone 15"
uv run python 07_cookie_warmup.py "iphone 15"
uv run python 08_recovery_time.py "iphone 15"
uv run python 09_keepalive_session.py "iphone 15"
uv run python 10_apptype_variants.py "iphone 15"
```

Запускать лучше с обычного RU-IP, без VPN/датацентров.
