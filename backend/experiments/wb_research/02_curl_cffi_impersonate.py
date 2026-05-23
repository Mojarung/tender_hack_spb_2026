from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import HEADERS, SEARCH_URL, Timer, params, query_from_argv, save_json

TLS_CASCADE = ("chrome131", "chrome", "safari17_2_ios")


async def main() -> int:
    import orjson
    from curl_cffi.requests import AsyncSession

    query = query_from_argv()
    print(f"[i] WB curl_cffi query={query!r}")

    failures = []
    for tls in TLS_CASCADE:
        print(f"[i] trying impersonate={tls}")
        with Timer() as timer:
            try:
                async with AsyncSession(impersonate=tls, timeout=10) as session:
                    resp = await session.get(SEARCH_URL, params=params(query), headers=HEADERS)
            except Exception as exc:
                print(f"[!] network error: {exc}")
                failures.append({"tls": tls, "error": repr(exc)})
                continue

        print(f"[i] status={resp.status_code} time={timer.elapsed_ms}ms bytes={len(resp.content)}")
        if resp.status_code != 200:
            failures.append({"tls": tls, "status": resp.status_code, "body": resp.text[:500]})
            continue

        body = orjson.loads(resp.content)
        products = body.get("products") or (body.get("data") or {}).get("products") or []
        print(f"[+] products={len(products)} tls={tls}")
        save_json("02_curl_cffi_ok", {"tls": tls, "count": len(products), "sample": products[:5]})
        return 0 if products else 2

    save_json("02_curl_cffi_block", {"failures": failures})
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
