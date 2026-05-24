"""04 — Click each card, capture the destination URL.

Google Shopping cards open the merchant page on click (new tab or same
tab — we discover which). Process:
  1. Extract top-N card data (name/price/seller/rating) — JS, no network
  2. For each card, click it, watch for a tab whose URL leaves google.com
  3. Filter: keep .ru domains only
  4. Verify: HEAD/GET the URL, drop 4xx/5xx
  5. Close the spawned tab, move to next card

Human-paced sleeps (1.5–3 s) between clicks to dodge Google's anti-spam.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc
from curl_cffi.requests import AsyncSession

from _common import OUT_DIR, PROFILE_DIR, info, ok, query_from_argv, save_json, section, warn

# Pull card metadata (no URLs yet).
EXTRACT_JS = r"""
(() => {
  const ruble = /\d[\d\s]*\s*(?:₽|руб)/i;
  const seen = new Set();
  const cards = [];
  for (const img of document.querySelectorAll('img')) {
    const w = img.naturalWidth || img.width || 0;
    if (w && w < 80) continue;
    let el = img;
    for (let d = 0; d < 10 && el; d++) {
      el = el.parentElement;
      if (!el) break;
      if (!ruble.test(el.innerText || '')) continue;
      if ((el.innerText || '').length > 1500) break;
      if (seen.has(el)) break;
      seen.add(el);
      cards.push(el);
      break;
    }
  }
  // De-dup adjacent expanded copies (mEooDb OUTd5d == same product expanded)
  // by name. Keep first instance.
  const out = [];
  const seenNames = new Set();
  for (const c of cards) {
    const txt = (c.innerText || '').replace(/About this result|Report a violation/g, '').trim();
    const lines = txt.split(/\n+/).map(s => s.trim()).filter(Boolean);
    // Heuristic line picker:
    //   first line = badge ("НИЗКАЯ ЦЕНА") OR title
    //   pick the longest line as title
    let title = '';
    for (const l of lines) {
      if (/^\d/.test(l)) continue;        // skip pure-numeric / price
      if (/^Низкая цена$|^Б\/у$|^Возврат|^Обычно/i.test(l)) continue;
      if (l.length > title.length) title = l;
    }
    if (!title || title.length < 4) continue;
    if (seenNames.has(title)) continue;
    seenNames.add(title);
    const priceMatch = txt.match(/(\d{1,3}(?:[ \s]\d{3})*(?:,\d+)?)\s*₽/);
    const price = priceMatch ? priceMatch[1].replace(/[\s ]/g, '').replace(',', '.').split('.')[0] : null;
    // Seller — line right after the price block, usually short (no rating)
    let seller = null;
    for (let i = 0; i < lines.length; i++) {
      if (/₽/.test(lines[i])) {
        for (let j = i + 1; j < lines.length; j++) {
          const l = lines[j];
          if (/^Обычно|^Возврат|^\d|и не только/i.test(l)) continue;
          if (l.length > 40) continue;
          seller = l; break;
        }
        break;
      }
    }
    // Rating "4,7(55 тыс.)"
    const rm = txt.match(/(\d[.,]\d)\s*\(([^)]+)\)/);
    const rating = rm ? parseFloat(rm[1].replace(',', '.')) : null;
    let reviewsTxt = rm ? rm[2] : null;
    let reviews_count = null;
    if (reviewsTxt) {
      const cm = reviewsTxt.match(/(\d+(?:[.,]\d+)?)\s*(тыс|тысяч|k|K)?/);
      if (cm) {
        let n = parseFloat(cm[1].replace(',', '.'));
        if (cm[2]) n *= 1000;
        reviews_count = Math.round(n);
      }
    }
    // Image (skip data: base64 — those are tiny placeholders)
    const img = c.querySelector('img:not([src^="data:"])');
    const image = img ? (img.getAttribute('src') || null) : null;
    // Stash the element index so we can click it later
    out.push({
      idx: out.length,
      title, price, seller, rating, reviews_count, image,
    });
    if (out.length >= 10) break;
  }
  // Tag the elements so JS click finds them via querySelector + index
  let i = 0;
  for (const c of cards) {
    if (i >= out.length) break;
    c.setAttribute('data-research-idx', String(i));
    i++;
  }
  return JSON.stringify(out);
})()
"""

CLICK_JS_TPL = r"""
(() => {
  const el = document.querySelector('[data-research-idx="%d"]');
  if (!el) return JSON.stringify({clicked: false});
  // Find the actual clickable inside — usually the whole tile
  el.click();
  return JSON.stringify({clicked: true});
})()
"""


async def click_capture(browser: Any, tab: Any, idx: int, timeout_s: float = 8.0) -> str | None:
    """Click card #idx, return the destination URL once it leaves google.com."""
    pre_tabs = list(browser.tabs)
    pre_url = await tab.evaluate("location.href", await_promise=False)
    pre_url = pre_url if isinstance(pre_url, str) else str(pre_url)

    raw = await tab.evaluate(CLICK_JS_TPL % idx, await_promise=False)
    res = json.loads(raw) if isinstance(raw, str) else raw
    if not res.get("clicked"):
        return None

    # Either a new tab opens (mostly Google) or current tab navigates.
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        # Check new tabs first
        new_tabs = [t for t in browser.tabs if t not in pre_tabs]
        if new_tabs:
            nt = new_tabs[0]
            try:
                u = await nt.evaluate("location.href", await_promise=False)
                u = u if isinstance(u, str) else str(u)
            except Exception:
                continue
            if u and "google.com" not in u and u != "about:blank":
                try:
                    await nt.close()
                except Exception:
                    pass
                return u
        # Or check if main tab navigated away
        try:
            cur = await tab.evaluate("location.href", await_promise=False)
            cur = cur if isinstance(cur, str) else str(cur)
        except Exception:
            continue
        if cur and "google.com" not in cur and cur != pre_url:
            # Don't leave the SERP tab on a merchant page; navigate back
            try:
                await tab.get(pre_url)
            except Exception:
                pass
            return cur
    return None


async def verify_alive(session: AsyncSession, url: str) -> bool:
    try:
        r = await session.get(url, timeout=10, allow_redirects=True)
    except Exception:
        return False
    if r.status_code >= 400:
        return False
    # Cheap "is this a product page?" sniff
    txt = r.text if isinstance(r.text, str) else ""
    return any(tok in txt.lower() for tok in ("купить", "товар", "₽", "руб", "корзин"))


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv()
    section(f"CLICK + CAPTURE — {query!r}")
    browser = await uc.start(
        headless=False, lang="ru-RU",
        user_data_dir=str(PROFILE_DIR.resolve()),
        browser_args=["--lang=ru-RU", "--window-size=1600,1000"],
    )
    try:
        url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=shop&hl=ru&gl=ru"
        info(f"navigating: {url}")
        tab = await browser.get(url)
        await asyncio.sleep(5)

        raw = await tab.evaluate(EXTRACT_JS, await_promise=False)
        cards = json.loads(raw) if isinstance(raw, str) else raw
        ok(f"extracted {len(cards)} unique cards (pre-click)")
        # safe_print — strip non-ascii so Windows cp1251 stdout doesn't crash
        def s(x):
            return str(x).encode("ascii", errors="replace").decode("ascii")
        for c in cards[:10]:
            print(f"  #{c['idx']} {s(c['title'])[:40]:40} {s(c.get('price') or '-'):>8} | seller={s(c.get('seller'))}")

        # Drop expanded-view dupes (seller=None means "click me to expand")
        # and click top-5 real cards with humanlike delays
        real_cards = [c for c in cards if c.get("seller")]
        info(f"real cards (seller != null): {len(real_cards)}")
        results: list[dict] = []
        for c in real_cards[:5]:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            info(f"click #{c['idx']} -> '{s(c['title'])[:40]}'")
            u = await click_capture(browser, tab, c["idx"])
            c["url"] = u
            results.append(c)
            print(f"  -> url: {s(u or '(none)')}")

        # Filter .ru + verify
        async with AsyncSession(impersonate="chrome") as session:
            for r in results:
                u = r.get("url")
                if not u:
                    r["status"] = "no_url"
                    continue
                host = (urlparse(u).hostname or "").lower()
                if not host.endswith(".ru"):
                    r["status"] = "not_ru"
                    continue
                alive = await verify_alive(session, u)
                r["status"] = "ok" if alive else "dead"

        ok("=== final ===")
        for r in results:
            print(f"  [{r.get('status','?'):7}] {s(r['title'])[:40]:40} | {s(r.get('url') or '(none)')[:60]}")
        save_json("04_click_capture", results)
    finally:
        await asyncio.sleep(1)
        browser.stop()
    return 0


from typing import Any  # delayed import for clean module-level

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
