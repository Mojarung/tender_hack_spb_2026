"""05 — characteristics endpoint (`pdpAtomicCharacteristics`).

PURPOSE
    The PDP page is huge (every widget). The trick is to ask for ONLY
    the characteristics container:
        /product/{slug}/?layout_container=pdpAtomicCharacteristics&layout_page_index=2

    Widget keys observed in the wild:
        webProductCharacteristics-*
        webShortCharacteristics-*
        webAttributes-*

    This script demonstrates that we can pull a structured attribute
    list for any product (closes the "characteristics only on WB" P0
    hole listed in CLAUDE.md).

USAGE
    cd ozon_research
    uv run python 05_characteristics_endpoint.py "/product/noutbuk-lenovo-..."
    uv run python 05_characteristics_endpoint.py 1715567830
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from _common import Timer, android_cookies, android_headers, err, info, ok, save_json, section, warn

BASE = "https://api.ozon.ru/composer-api.bx/page/json/v2"

# Tried in order. Different products surface characteristics in different
# containers; the first one that returns non-empty wins.
LAYOUT_CONTAINERS = [
    "pdpAtomicCharacteristics",
    "pdpPage2Column",
    "pdppage2copy",  # full PDP — fallback if the focused containers are empty
]


def _product_path(arg: str) -> str:
    arg = arg.strip()
    if arg.startswith("/product"):
        return arg.rstrip("/")
    if arg.isdigit():
        return f"/products/{arg}"
    if arg.startswith("http"):
        from urllib.parse import urlparse
        return urlparse(arg).path.rstrip("/")
    return f"/product/{arg.strip('/')}"


def _walk_char_widgets(widget_states: dict) -> dict:
    import orjson

    out: dict = {}
    for key, value in widget_states.items():
        if not isinstance(value, str):
            continue
        kl = key.lower()
        if not any(t in kl for t in ("characteristic", "attribute", "shortcharacter", "techspec")):
            continue
        try:
            out[key] = orjson.loads(value)
        except orjson.JSONDecodeError:
            out[key] = {"_raw_preview": value[:500]}
    return out


def _flatten_attributes(widgets: dict) -> list[tuple[str, str]]:
    """Best-effort: walk the nested atomic structure looking for
    name/value pairs. Ozon stores them as text atoms in various shapes."""
    pairs: list[tuple[str, str]] = []

    def _visit(node) -> None:
        if isinstance(node, dict):
            # The most common shape: {"name": "...", "values": [{"text": "..."}, ...]}
            name = node.get("name") or node.get("title") or node.get("label")
            values = node.get("values") or node.get("value") or node.get("texts")
            if isinstance(name, str) and values:
                if isinstance(values, list):
                    text = ", ".join(
                        v.get("text", str(v)) if isinstance(v, dict) else str(v) for v in values
                    )
                elif isinstance(values, dict):
                    text = values.get("text") or str(values)
                else:
                    text = str(values)
                if name.strip() and text.strip():
                    pairs.append((name.strip(), text.strip()))
            for v in node.values():
                _visit(v)
        elif isinstance(node, list):
            for v in node:
                _visit(v)

    _visit(widgets)
    # De-dup preserving order
    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for p in pairs:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


async def _try_container(s, base_path: str, container: str) -> dict | None:
    from urllib.parse import quote as _q
    import orjson

    sub = f"{base_path}/?layout_container={container}&layout_page_index=2"
    url = f"{BASE}?url={_q(sub, safe='')}"
    info(f"  trying layout_container={container}")
    resp = await s.get(url, headers=android_headers())
    if resp.status_code != 200:
        warn(f"  {container} → HTTP {resp.status_code}")
        return None
    try:
        body = orjson.loads(resp.content)
    except orjson.JSONDecodeError:
        warn(f"  {container} → non-JSON")
        return None
    widgets = _walk_char_widgets(body.get("widgetStates") or {})
    if not widgets:
        warn(f"  {container} → no char widgets")
        return None
    ok(f"  {container} → {len(widgets)} char widget(s)")
    return widgets


async def main() -> int:
    section("CHARACTERISTICS ENDPOINT — layout_container=pdpAtomicCharacteristics")

    if len(sys.argv) < 2:
        err("usage: python 05_characteristics_endpoint.py '/product/slug-or-id/'")
        return 3

    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        err("curl_cffi not installed")
        return 3

    base_path = _product_path(sys.argv[1])
    info(f"product = {base_path}")

    with Timer() as t:
        async with AsyncSession(impersonate="chrome131_android", timeout=20) as s:
            for k, v in android_cookies().items():
                s.cookies.set(k, v)
            widgets: dict | None = None
            for cont in LAYOUT_CONTAINERS:
                widgets = await _try_container(s, base_path, cont)
                if widgets:
                    break

    info(f"time    = {t.elapsed_ms} ms")
    if not widgets:
        err("no characteristics widgets in any container")
        return 1

    attrs = _flatten_attributes(widgets)
    ok(f"flattened {len(attrs)} attribute pair(s)")
    for name, value in attrs[:20]:
        print(f"   • {name}: {value}")
    if len(attrs) > 20:
        info(f"   …and {len(attrs) - 20} more (see saved JSON)")

    path_out = save_json("05_chars_ok", {"product": base_path, "attributes": attrs, "raw_widgets": widgets})
    ok(f"saved → {path_out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
