"""Shared helpers for the Ozon research scripts.

These scripts intentionally bypass pytest and the project's DI — each
file is a standalone smoke test that you can `python -m` from
`backend/`. Designed to be run by a human on a clean Russian IP (not in
CI, not under a VPN — Ozon serves a hard-block to most VPN ranges).

Nothing here imports from `pricepulse.*` — the research scripts must be
runnable even when the project venv is half-broken.
"""

from __future__ import annotations

import json
import secrets
import string
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).parent / "_out"
OUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Pretty terminal output (no Rich/colorama — we want zero extra deps)
# ---------------------------------------------------------------------------
def _supports_color() -> bool:
    return sys.stdout.isatty() and sys.platform != "win32" or "WT_SESSION" in __import__("os").environ


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
# Cookie / header generation (from research findings)
# ---------------------------------------------------------------------------
_ABT_ALPHABET = string.ascii_letters + string.digits + "-_"


def gen_abt_data(length: int = 500) -> str:
    """`abt_data` — A/B-test bucket cookie. Format observed in production
    (Churkashh/ozon-pinneaples Dec 2024): "7." + ~475-520 random chars."""
    body = "".join(secrets.choice(_ABT_ALPHABET) for _ in range(length))
    return f"7.{body}"


def gen_x_o3_fp() -> str:
    """`x-o3-fp` — device fingerprint header. 17-hex with a "1." prefix
    (form seen in the Churkashh scraper). Stable per session is fine."""
    return f"1.{secrets.token_hex(8)}"


def gen_mobile_gaid() -> str:
    """`MOBILE-GAID` — Google Advertising ID, UUID-4 per session."""
    return str(uuid.uuid4())


# Single source of truth for the Android-app headers everyone agrees on.
# Pinned to Ozon Android v17.48.0 (build 2528) — the version used by the
# only confirmed-working 2024 scraper. Ozon does NOT validate version
# freshness on the public composer-api path (JTJag's 16.28.x worked for
# years).
APP_VERSION = "17.48.0"
APP_BUILD = "2528"


def android_headers(
    *,
    fp: str | None = None,
    gaid: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Full `ozonapp_android` header set with the four entries our
    production scrapers/ozon.py is currently missing."""
    h: dict[str, str] = {
        "Accept": "application/json; charset=utf-8",
        "Accept-Language": "ru",
        "Host": "api.ozon.ru",
        "User-Agent": f"ozonapp_android/{APP_VERSION}+{APP_BUILD}",
        "x-o3-app-name": "ozonapp_android",
        "x-o3-app-version": APP_VERSION,
        "x-o3-device-type": "mobile",
        # --- THE FOUR THAT THE CURRENT CODE OMITS ---
        "MOBILE-GAID": gaid or gen_mobile_gaid(),
        "MOBILE-LAT": "0",
        "x-o3-fp": fp or gen_x_o3_fp(),
        "x-o3-sample-trace": "false",
    }
    if extra:
        h.update(extra)
    return h


def android_cookies() -> dict[str, str]:
    """Minimum cookie set for anonymous mobile-API reads."""
    return {
        "x-o3-app-name": "ozonapp_android",
        "abt_data": gen_abt_data(),
    }


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------
def save_json(name: str, payload: Any) -> Path:
    """Drop a JSON snapshot in `_out/` — handy for `jq` and grep later."""
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_bytes(name: str, data: bytes, suffix: str = ".bin") -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}{suffix}"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Tiny timing helper
# ---------------------------------------------------------------------------
class Timer:
    def __enter__(self) -> "Timer":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self.t0) * 1000)


# ---------------------------------------------------------------------------
# Default query — picked from the hackathon jury categories
# (одежда / шины / оргтехника per final_presa.pdf p.3).
# Override with `python <script>.py "<query>"`.
# ---------------------------------------------------------------------------
DEFAULT_QUERY = "ноутбук lenovo"


def query_from_argv(default: str = DEFAULT_QUERY) -> str:
    return " ".join(sys.argv[1:]).strip() or default


__all__ = [
    "APP_BUILD",
    "APP_VERSION",
    "DEFAULT_QUERY",
    "OUT_DIR",
    "Timer",
    "android_cookies",
    "android_headers",
    "err",
    "gen_abt_data",
    "gen_mobile_gaid",
    "gen_x_o3_fp",
    "info",
    "ok",
    "query_from_argv",
    "save_bytes",
    "save_json",
    "section",
    "warn",
]
