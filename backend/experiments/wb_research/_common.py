from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).parent / "_out"
OUT_DIR.mkdir(exist_ok=True)

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"
DEFAULT_DEST = "-1257786"
DEFAULT_QUERY = "iphone 15"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


def params(query: str, page: int = 1, dest: str = DEFAULT_DEST) -> dict[str, str]:
    return {
        "ab_testid": "false",
        "appType": "1",
        "curr": "rub",
        "dest": dest,
        "hide_dtype": "13",
        "lang": "ru",
        "page": str(page),
        "query": query,
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
        "suppressSpellcheck": "false",
    }


def query_from_argv(default: str = DEFAULT_QUERY) -> str:
    return " ".join(sys.argv[1:]).strip() or default


def save_json(name: str, payload: Any) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class Timer:
    def __enter__(self) -> Timer:
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self.t0) * 1000)
