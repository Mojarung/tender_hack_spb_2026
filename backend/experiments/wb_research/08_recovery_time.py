"""Замер реального времени восстановления после 429.

В production у нас стоит `_COOLDOWN_S = 120s`, но это эвристика. Реальный
WB recovery может быть короче или длиннее. Скрипт пингует search.wb.ru
лёгким plain-запросом раз в `PROBE_EVERY_S` секунд до первого 200 OK
или до `MAX_TOTAL_S`. Логирует каждый probe.

Запускать **только** когда уже получен 429 (любой пред. эксперимент).
Если первый probe вернул 200 — значит блок снят, мы фиксируем 0s recovery.

Безопасно: 1 запрос раз в N секунд, plain shape.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import SEARCH_URL, params, query_from_argv, save_json

PLAIN_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

PROBE_EVERY_S = 10.0
MAX_TOTAL_S = 240.0


async def main() -> int:
    import httpx

    query = query_from_argv()
    print(f"[i] WB recovery probe every {PROBE_EVERY_S}s, max {MAX_TOTAL_S}s")

    rows: list[dict] = []
    t0 = time.monotonic()
    recovered_at: float | None = None
    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=10) as client:
        i = 0
        while True:
            elapsed = time.monotonic() - t0
            if elapsed > MAX_TOTAL_S:
                break
            try:
                resp = await client.get(SEARCH_URL, params=params(query))
                status = resp.status_code
                x_pow = resp.headers.get("x-pow")
                retry_after = resp.headers.get("retry-after")
            except httpx.HTTPError as exc:
                status = -1
                x_pow = None
                retry_after = None
                print(f"[!] probe[{i}] error: {exc}")

            print(f"[i] probe[{i}] t+{elapsed:6.1f}s status={status} x-pow={x_pow!r} retry-after={retry_after}")
            rows.append({"i": i, "t_offset_s": round(elapsed, 2), "status": status, "x_pow": x_pow, "retry_after": retry_after})

            if status == 200:
                recovered_at = elapsed
                break

            await asyncio.sleep(PROBE_EVERY_S)
            i += 1

    save_json(
        "08_recovery_time",
        {
            "query": query,
            "probe_every_s": PROBE_EVERY_S,
            "recovered_at_s": recovered_at,
            "rows": rows,
        },
    )
    if recovered_at is not None:
        print(f"[+] recovered after ~{recovered_at:.1f}s")
    else:
        print("[!] no recovery within budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
