# Firecrawl на наших таргетах — тест-репорт

Дата: 2026-05-21, 12:15 МСК. Hosted Firecrawl (`api.firecrawl.dev`), proxy=`stealth`, waitFor=5–7s, JSON-extract по строгой Pydantic-схеме. Запрос: «iphone 15 128 gb».

**TL;DR:** Firecrawl **не справляется** с тремя из четырёх таргетов. Использовать его как **полную замену** нашего стека нельзя. Подходит как:
1. L3 fallback в свободной квоте на отдельных сложных случаях
2. Основной инструмент для **4-го «плавающего» источника** (там как раз LLM-extraction раскрывается)

Идём дальше по нашей собственной cascade (см. [anti-bot.md](./anti-bot.md)).

---

## Что вышло

### 1. Wildberries — `200 OK`, но **галлюцинации**

URL: `https://www.wildberries.ru/catalog/0/search.aspx?search=iphone+15+128`.

Firecrawl получил HTML, но **search results на WB рендерятся через React после загрузки страницы**, а `waitFor=5000` не дождался данных. В extract-вызов попал каркас без товаров — LLM **выдумал** правдоподобный JSON:

```json
{"name": "Product 1", "price": 1999, "brand": "Brand A",
 "product_url": "https://wildberries.ru/product1", ...}
```

Это **полный мусор**: ни одного реального товара, цены случайные, URL несуществующие. Метаданные страницы (`og:title`) указывают что отдали главную WB, не страницу поиска. **Кредитов потрачено: 9**.

**Вывод:** WB через HTML-scrape не имеет смысла. Используем только `search.wb.ru/v18` JSON-эндпоинт (см. [anti-bot.md §5.1](./anti-bot.md)).

### 2. Ozon — `403 Forbidden` + капча

URL: `https://www.ozon.ru/search/?text=iphone+15+128+gb`.

`statusCode=403`, `<title>Antibot Captcha</title>`. Firecrawl stealth **не пробил** собственный антибот Ozon. LLM в extract-вызов прислал страницу капчи и **выдумал JSON про пазлы** (распознал capcha-картинку как «пазл-товар»):

```json
{"name": "Пазл 1000 элементов", "price": 1500, "brand": "PuzzleBrand", ...}
```

**Кредитов потрачено: 9**. Цена нулевая, результат бесполезен.

**Вывод:** Подтверждается стратегия из [anti-bot.md §5.2](./anti-bot.md) — для Ozon идём через `api.ozon.ru/composer-api.bx` с mobile UA `ozonapp_android/17.48.0+2528`. Firecrawl HTML здесь нерелевантен.

### 3. Yandex Market — `200 OK`, **пустой результат**

URL: `https://market.yandex.ru/search?text=iphone+15+128+gb`.

Страница **открылась**, в метаданных есть агрегированная информация: `lowPrice="49222"`, `highPrice="57078"`, `offerCount="8"`. Но **JSON extract вернул пустой объект** — LLM не смог распознать структуру SSR-данных Маркета.

Это **частичная победа**: stealth-mode прошёл SmartCaptcha, доступ получен. Но extract-промпт оказался слишком общим. Можно было бы выжать данные если:
- скрейпить `<script type="application/ld+json">` напрямую (Schema.org `Product`)
- или extract по точной схеме с указанием `data-zone-name="snippet-card"`

**Кредитов потрачено: 9**.

**Вывод:** Для YM Firecrawl потенциально подходит как **L3-fallback на сложные case** (когда наш Camoufox валится), но не как основной — слишком дорого на 1 запрос (9 кр.) и непредсказуемо. Стратегия из [anti-bot.md §5.3](./anti-bot.md) (Camoufox + warm cookies) остаётся главной.

### 4. Megamarket — `403 Forbidden`

URL: `https://megamarket.ru/catalog/search/?q=iphone+15+128+gb`.

`statusCode=403`, страница «Упс…». Cloudflare/Qrator на стороне Megamarket. Stealth не пробил.

**Кредитов потрачено: 9**. Результат пустой.

**Вывод:** Megamarket как 4-й источник — через прямой `api/mobile/v2/catalogService/catalog/search` с прогревом `mg_sid` cookie (см. [scrapers/megamarket.py](../src/pricepulse/scrapers/megamarket.py)). Firecrawl здесь не помогает.

---

## Итоговая оценка

| Таргет | Firecrawl результат | Наша стратегия (см. anti-bot.md) |
|---|---|---|
| Wildberries | ❌ галлюцинации | ✅ `search.wb.ru/v18` JSON, без HTML |
| Ozon | ❌ 403 + капча | ✅ `api.ozon.ru/composer-api` с mobile UA |
| Yandex Market | ⚠️ страница есть, extract пустой | ✅ Camoufox + warm `spravka` |
| Megamarket | ❌ 403 | ✅ Прямой mobile API + cookie warmup |

**Стоимость теста:** 36 кредитов (≈$0.05 на free-tier 500 кр./мес). Из них **полезной информации — 0**.

### Когда Firecrawl всё-таки нужен

1. **4-й «плавающий» источник** — SearXNG → top-N URLs → Firecrawl scrape с LLM-extract по нашей JSON-схеме. **Это его killer use-case** для нашего проекта.
2. **Документация и публичные блоги** — для пополнения тезауруса синонимов / нормализации запросов мы можем использовать Firecrawl на vc.ru, Habr и так далее. Бесплатно в квоте.
3. **MCP-инструмент для агента** (этот) — для ресёрча, как сейчас. Хорошо себя показал на ollama.com, github.com, gethomepage.dev.

### Что не делать

- ❌ Не использовать Firecrawl как драйвер для основных 4 адаптеров — дорого, ненадёжно, теряем контроль.
- ❌ Не пытаться обойти Ozon/Megamarket антибот через Firecrawl stealth — он не пробивает.
- ❌ Не доверять LLM-extract без проверки структуры — галлюцинации, как на WB, тихо отравят данные.

---

## Источники

- Live-тест 2026-05-21 через `mcp__firecrawl-mcp__firecrawl_scrape`, proxy=stealth.
- Подтверждает рекомендации [anti-bot.md](./anti-bot.md) и [free-mode.md](./free-mode.md).
