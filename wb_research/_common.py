"""Shared helpers for the Wildberries research scripts.

Mirrors `ozon_research/_common.py` — zero project imports, runnable
even when the main backend venv is broken. Designed to be sourced by
each diagnostic script via `sys.path.insert(0, str(Path(__file__).parent))`.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).parent / "_out"
OUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Terminal colours (no extra deps — straight ANSI; safe on modern Windows
# Terminal / VS Code / WSL bash). Auto-disable when stdout isn't a TTY.
# ---------------------------------------------------------------------------
def _supports_color() -> bool:
    import os

    return (
        sys.stdout.isatty() and sys.platform != "win32"
        or "WT_SESSION" in os.environ
    )


_COLOR = _supports_color()


def _wrap(s: str, code: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


def info(msg: str) -> None:
    print(_wrap(f"[i] {msg}", "36"))


def ok(msg: str) -> None:
    print(_wrap(f"[+] {msg}", "32"))


def warn(msg: str) -> None:
    print(_wrap(f"[!] {msg}", "33"))


def err(msg: str) -> None:
    print(_wrap(f"[-] {msg}", "31"))


def section(title: str) -> None:
    bar = "=" * max(8, 60 - len(title))
    print(_wrap(f"\n== {title} {bar}", "1;35"))


# ---------------------------------------------------------------------------
# Browser-class headers for `wb.ru` family hosts. All public endpoints
# tolerate plain httpx, but `feedbacks{1,2}.wb.ru` occasionally 403s
# without Origin/Referer. Match what the real wildberries.ru tab sends.
# ---------------------------------------------------------------------------
WB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}


# ---------------------------------------------------------------------------
# WB-specific URL helpers
# ---------------------------------------------------------------------------
_BASKET_RANGES: tuple[tuple[int, str], ...] = (
    (143, "01"), (287, "02"), (431, "03"), (719, "04"), (1007, "05"),
    (1061, "06"), (1115, "07"), (1169, "08"), (1313, "09"), (1601, "10"),
    (1655, "11"), (1919, "12"), (2045, "13"), (2189, "14"), (2405, "15"),
    (2621, "16"), (2837, "17"), (3053, "18"), (3269, "19"), (3485, "20"),
    (3701, "21"), (3917, "22"), (4133, "23"), (4349, "24"), (4565, "25"),
    (4877, "26"), (5189, "27"), (5501, "28"), (5813, "29"), (6125, "30"),
    (6437, "31"), (6749, "32"), (7061, "33"), (7373, "34"), (7685, "35"),
)


def basket_for(nm_id: int) -> str:
    """Compute basket shard (`01`..`35`) by nm_id `vol` range. Past the
    last known range we extrapolate; production code should iterate ±5
    on 404 (WildberriesToolsMCP pattern, see README)."""
    vol = nm_id // 100_000
    for upper, shard in _BASKET_RANGES:
        if vol <= upper:
            return shard
    # Extrapolation
    return f"{36 + (vol - _BASKET_RANGES[-1][0]) // 312:02d}"


def card_json_url(nm_id: int, shard: str | None = None) -> str:
    """Full URL of `info/ru/card.json` for a given nm_id."""
    if shard is None:
        shard = basket_for(nm_id)
    vol = nm_id // 100_000
    part = nm_id // 1_000
    return f"https://basket-{shard}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"


def image_url(nm_id: int, idx: int = 1, *, size: str = "big", shard: str | None = None) -> str:
    """Gallery image URL. Pass `idx` 1..N (where N comes from card.json `media.photo_count`)."""
    if shard is None:
        shard = basket_for(nm_id)
    vol = nm_id // 100_000
    part = nm_id // 1_000
    return f"https://basket-{shard}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/{size}/{idx}.webp"


# ---------------------------------------------------------------------------
# Feedback host picker — CRC-16/ARC mod 100, matches what wildberries.ru
# actually does in JS. Both shards return identical data; this just
# matches the canonical request the browser would make.
# ---------------------------------------------------------------------------
def _crc16_arc(imt_id: int) -> int:
    crc = 0
    for b in imt_id.to_bytes(8, "little"):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def feedbacks_host(imt_id: int) -> str:
    return "feedbacks2.wb.ru" if _crc16_arc(imt_id) % 100 >= 50 else "feedbacks1.wb.ru"


def feedback_photo_urls(key: str) -> dict[str, str]:
    """key looks like '6/d7a25475-cd60-412a-985f-11007bf8d84f'.
    Returns mini / full webp + jpg fallback URLs."""
    shard_str, uuid = key.split("/", 1)
    shard = int(shard_str)
    base = f"https://feedback-{shard:02d}.wbbasket.ru/{uuid}"
    return {
        "mini": f"{base}/ms.webp",
        "full": f"{base}/fs.webp",
        "jpg":  f"{base}/fs.jpg",
    }


def feedback_video_urls(video_id: str) -> dict[str, str]:
    """video_id looks like '3/f2d03473-7685-45d3-98e9-1f7a002625a8'.
    Returns HLS playlist + preview poster. Videos are HLS-segmented —
    no single .mp4 (ffmpeg-mux needed if you want one)."""
    shard_str, uuid = video_id.split("/", 1)
    shard = int(shard_str)
    base = f"https://videofeedback{shard:02d}.wbbasket.ru/{uuid}"
    return {
        "preview": f"{base}/preview.webp",
        "hls":     f"{base}/index.m3u8",
    }


# ---------------------------------------------------------------------------
# region_id (Yandex `lr`) → WB `dest` mapping (verified against the
# Insomnia collection at teocci/go-fiber-web + wildberries.ru network
# traffic). Past these 12 cities the mapping is undocumented — fall
# back to Moscow until we scrape live.
# ---------------------------------------------------------------------------
YANDEX_LR_TO_WB_DEST: dict[int, int] = {
    213: -1257786,   # Москва
    2:   -1281180,   # СПб
    54:  -1106193,   # Екатеринбург
    65:  -364763,    # Новосибирск
    35:  -2162196,   # Краснодар
    43:  -1255942,   # Казань
    47:  -1255987,   # Нижний Новгород
    39:  -993516,    # Ростов-на-Дону
    172: -1255871,   # Уфа
    51:  -1255936,   # Самара
    193: -1278972,   # Воронеж
    50:  -1216601,   # Пермь
}

WB_DEFAULT_DEST = YANDEX_LR_TO_WB_DEST[213]

# Magic Moscow `regions=` list — works as a no-op union of WB's
# biggest delivery regions, so passing it everywhere is safer than
# computing per-city.
WB_REGIONS = "80,64,38,4,83,33,68,70,69,30,86,75,40,1,22,66,31,48,110,71"


def dest_for(region_id: int) -> int:
    return YANDEX_LR_TO_WB_DEST.get(region_id, WB_DEFAULT_DEST)


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------
def save_json(name: str, payload: Any) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_bytes(name: str, data: bytes, suffix: str = ".bin") -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}{suffix}"
    path.write_bytes(data)
    return path


class Timer:
    def __enter__(self) -> "Timer":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self.t0) * 1000)


# ---------------------------------------------------------------------------
# Default query for jury-friendly categories (одежда / шины / оргтехника
# per final_presa.pdf p.3). Pass any string as argv[1] to override.
# ---------------------------------------------------------------------------
DEFAULT_QUERY = "шины 205 55 R16"


def query_from_argv(default: str = DEFAULT_QUERY) -> str:
    return " ".join(sys.argv[1:]).strip() or default


__all__ = [
    "DEFAULT_QUERY",
    "OUT_DIR",
    "Timer",
    "WB_DEFAULT_DEST",
    "WB_HEADERS",
    "WB_REGIONS",
    "YANDEX_LR_TO_WB_DEST",
    "basket_for",
    "card_json_url",
    "dest_for",
    "err",
    "feedback_photo_urls",
    "feedback_video_urls",
    "feedbacks_host",
    "image_url",
    "info",
    "ok",
    "query_from_argv",
    "save_bytes",
    "save_json",
    "section",
    "warn",
]
