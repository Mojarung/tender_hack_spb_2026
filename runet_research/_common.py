"""Shared helpers for the Runet (Yandex shopping) research scripts.

Mirrors `wb_research/_common.py` — zero project imports, runnable even
when the main backend venv is broken.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).parent / "_out"
OUT_DIR.mkdir(exist_ok=True)
PROFILE_DIR = Path(__file__).parent / ".profile_yandex"


# ---------------------------------------------------------------------------
# Terminal colours (auto-disabled when stdout isn't a TTY)
# ---------------------------------------------------------------------------
def _supports_color() -> bool:
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
# Output capture — JSON / bytes / screenshots all go under _out/
# ---------------------------------------------------------------------------
def save_json(name: str, payload: Any) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_html(name: str, html: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"{stamp}_{name}.html"
    path.write_text(html, encoding="utf-8")
    return path


class Timer:
    def __enter__(self) -> "Timer":
        self._t = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        pass

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t) * 1000)


# ---------------------------------------------------------------------------
# CLI: read query from argv or default to a recognisable test phrase.
# ---------------------------------------------------------------------------
def query_from_argv(default: str = "iphone 15 128") -> str:
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:]).strip()
    return default


__all__ = [
    "OUT_DIR", "PROFILE_DIR",
    "info", "ok", "warn", "err", "section",
    "save_json", "save_html", "Timer", "query_from_argv",
]
