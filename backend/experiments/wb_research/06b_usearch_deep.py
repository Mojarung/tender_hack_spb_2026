"""Deep dive в u-search.wb.ru: проверить, что реально отдаёт shard.

Из 06_endpoint_shards: u-search дал status=200 с `x-pow: status=invalid`,
но products=None (парсер ничего не нашёл). Нужно понять структуру тела —
это полноценные результаты или заглушка.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import Timer, params, query_from_argv, save_json

PLAIN_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


async def main() -> int:
    import httpx

    query = query_from_argv()
    url = "https://u-search.wb.ru/exactmatch/ru/common/v18/search"

    async with httpx.AsyncClient(http2=True, headers=PLAIN_HEADERS, timeout=15) as client:
        with Timer() as t:
            resp = await client.get(url, params=params(query))

    print(f"[i] u-search status={resp.status_code} ms={t.elapsed_ms} bytes={len(resp.content)}")
    print(f"[i] x-pow={resp.headers.get('x-pow')}")
    body = None
    structure: dict = {}
    try:
        body = resp.json()
        if isinstance(body, dict):
            structure["top_level_keys"] = list(body.keys())
            for k, v in body.items():
                if isinstance(v, dict):
                    structure[f"{k}.keys"] = list(v.keys())
                if isinstance(v, list):
                    structure[f"{k}.len"] = len(v)
                    if v and isinstance(v[0], dict):
                        structure[f"{k}[0].keys"] = list(v[0].keys())[:20]
    except Exception as exc:
        structure["parse_error"] = str(exc)
        structure["text_head"] = resp.text[:500]

    print("[i] structure:")
    for k, v in structure.items():
        print(f"   {k}: {v}")

    save_json(
        "06b_usearch_deep",
        {
            "status": resp.status_code,
            "x_pow": resp.headers.get("x-pow"),
            "headers": dict(resp.headers),
            "structure": structure,
            "body_sample": (
                {k: (v if not isinstance(v, list) else v[:2]) for k, v in body.items()}
                if isinstance(body, dict)
                else None
            ),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
