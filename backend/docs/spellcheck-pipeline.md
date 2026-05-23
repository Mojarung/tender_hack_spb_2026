# Spell-correction pipeline — архитектура и задержки

Как поисковый запрос проходит нормализацию и сколько занимает каждый шаг.
Замеры — dev-машина (Windows / Docker Desktop), 2026-05-23, против живого
контейнера `pricepulse-spellcheck`.

## Диаграмма

```
                  Search request: raw query
                  ────────────────────────────
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  enrichment/normalize.py :: normalize_query()               │
    │                                                             │
    │      ┌─ Redis lookup  normalize:v1:{int(fix)}:{sha1(raw)}   │
    │      │                                            ~1-2 ms   │
    │      │                                                      │
    │      ├─ HIT  ─► return cached NormalizedQuery               │
    │      │                                                      │
    │      └─ MISS ▼                                              │
    │                                                             │
    │   1. _clean              Unicode NFKC, lowercase    ~6 µs   │
    │   2. correct_phrase      RapidFuzz vs brand dict   ~90 µs   │
    │   3. SpellCheck /fix     SAGE FRED-T5 (HTTP) ~500-900 ms ⚠  │
    │   4. translate           RU→EN brand mapping        ~5 µs   │
    │   5. synonym_alternates  pymorphy3 + thesaurus     ~1.3 ms  │
    │                                                             │
    │      ─ Redis SET (24h TTL) ─────────────────────── ~1-2 ms  │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                  NormalizedQuery
                  ├ normalized            (what hits the marketplaces)
                  ├ expansions[]          (audit notes for UI)
                  └ alternates[]          (synonym retry list)
```

## Что происходит внутри SpellCheck `/fix` (контейнер на :8095)

```
              POST /fix {"text": "..."}
                          │
                          ▼
    ┌──────────────────────────────────────────────────────┐
    │  spellcheck/server.py (FastAPI)                      │
    │                                                      │
    │   tokenizer.encode (SentencePiece)        ~2-5 ms    │
    │                                                      │
    │   model.generate(beam=4, max_len ≈ 1.5×n)            │
    │     ├─ T5 encoder pass                  ~50-100 ms   │
    │     └─ T5 beam decoder × num_beams    ~400-700 ms ⚠  │
    │                                                      │
    │   tokenizer.batch_decode                  ~1-2 ms    │
    │                                                      │
    │   post-process                                       │
    │     ├─ strip trailing `.!?…`              ~1 µs      │
    │     └─ lowercase first char (search-friendly) ~1 µs  │
    │                                                      │
    │   JSON response                                      │
    └──────────────────────────────────────────────────────┘
                          │
                          ▼
              {"original": "...", "fixed": "..."}
    Total HTTP roundtrip: 500-900 ms (CPU) / 50-100 ms (GPU)
```

## Сводка по задержкам

| Стадия | Cold path | Cache hit | Замечание |
|---|---|---|---|
| Redis GET (lookup) | 1–2 ms | 1–2 ms | docker-сеть, очень быстро |
| `_clean` | ~6 µs | — | regex + NFKC |
| `correct_phrase` | ~90 µs | — | RapidFuzz против ~60 бренд-токенов |
| **SpellCheck `/fix` (HTTP)** | **~600–900 ms** | — | **SAGE T5 beam=4 на CPU** (60% веса в pipeline) |
| `translate` | ~5 µs | — | per-token dict lookup |
| `synonym_alternates` | ~1.3 ms | — | pymorphy3 lemma + thesaurus |
| Redis SET (24h TTL) | 1–2 ms | — | при first compute |
| **Итого cold** | **~600–900 ms** | — | SAGE доминирует |
| **Итого cache hit** | — | **~1–2 ms** | **400–900× ускорение** |

Реальный замер cold/hit на dev-машине:
```
normalize_query (no cache)           882.50 ms   (avg of 20 runs)
normalize_query (cache HIT)            0.01 ms   (avg of 20 runs)
```

В замере «cache HIT» — fake in-memory кэш, без сетевого роундтрипа. На
живом Redis в docker-сети добавится ~1–2 мс на одно `GET`.

## Стратегия кэша

- **Уровень:** весь результат `normalize_query`. Один Redis-ключ — один
  скэшированный `NormalizedQuery` (со всеми expansions + alternates).
- **Ключ:** `normalize:v1:{int(fix)}:{sha1(raw)}` — версионированный,
  чтобы менять схему без миграции. `fix=True`/`fix=False` живут раздельно.
- **TTL:** 24 часа. Нормализация детерминистична per code version —
  можно жить долго. Изменение кода нормализации = бамп `_CACHE_VERSION`
  в `normalize.py`.
- **Defensive:** любой `cache.get`/`cache.set` failure → fall-through к
  вычислению. Redis может лечь — поиск продолжит работать.
- **Сценарий жюри:** один и тот же тестовый запрос за демо — первый раз
  ~900 мс, второй+ ~1 мс.

## Где это вшито в код

| Компонент | Файл |
|---|---|
| Pipeline + cache | `backend/src/pricepulse/enrichment/normalize.py` |
| HTTP-клиент к SAGE | `backend/src/pricepulse/enrichment/spellcheck_client.py` |
| SAGE микросервис | `backend/spellcheck/{Dockerfile,server.py}` |
| Бренд-словарь + translit | `backend/src/pricepulse/enrichment/{thesaurus,typos}.py` |
| Тезаурус синонимов | `backend/src/pricepulse/enrichment/{synonym_thesaurus,morphology}.py` |
| Оркестратор (proxy кэша) | `backend/src/pricepulse/orchestrator/search.py` — `SearchOrchestrator.__init__` |
| Тесты кэша | `backend/tests/unit/test_normalize_cache.py` (5 тестов) |
