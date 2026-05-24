"""05 — Throw every click strategy at one Google Shopping card and see
which one actually lands a merchant URL.

Strategies tested (in order, on a fresh card each time):
  S1: nodriver Element.mouse_click
  S2: CDP Input.dispatchMouseEvent (Moved → Pressed → Released)
  S3: focus the card + CDP keyDown 'Enter'
  S4: dispatch JS click() on each clickable descendant
  S5: hover then click on the title text node specifically

After each click attempt: poll for new tabs, same-tab nav, OR newly
visible <a href=http*> in the DOM (Google overlay panel exposes a
"Visit site" link with the real merchant URL).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent))

import nodriver as uc

from _common import OUT_DIR, PROFILE_DIR, info, ok, query_from_argv, save_json, section


TAG_CARDS_JS = r"""
(() => {
  const ruble = /\d[\d\s]*\s*(?:₽|руб)/i;
  const seen = new Set();
  const out = [];
  for (const img of document.querySelectorAll('img')) {
    let el = img;
    for (let d = 0; d < 10 && el; d++) {
      el = el.parentElement;
      if (!el) break;
      if (!ruble.test(el.innerText || '')) continue;
      if ((el.innerText || '').length > 1500) break;
      if (seen.has(el)) break;
      seen.add(el);
      const idx = out.length;
      el.setAttribute('data-probe-idx', String(idx));
      out.push({idx, title: (el.innerText || '').split('\n').find(l => !/^\d/.test(l) && l.length > 5) || ''});
      break;
    }
    if (out.length >= 6) break;
  }
  return JSON.stringify(out);
})()
"""

# After a click, scan for anything that looks like a merchant URL.
CAPTURE_JS = r"""
(() => {
  // Anything with /aclk? or shopping/product/ — those are Google redirects
  // that transparently land the user on the merchant.
  const out = {aclk: [], merchant: [], overlay: false};
  for (const a of document.querySelectorAll('a[href]')) {
    const h = (a.href || '').trim();
    if (!h.startsWith('http')) continue;
    if (h.includes('/aclk?')) out.aclk.push(h);
    if (h.includes('shopping/product/')) out.aclk.push(h);
    if (!/google\.com|gstatic\.com/i.test(h)) out.merchant.push(h);
  }
  // Overlay detection — Google's "product details" panel
  const overlay = document.querySelector(
    '[role=dialog], [class*=KnafFb i], [class*=pla-unit i], [aria-modal=true]'
  );
  if (overlay) out.overlay = true;
  return JSON.stringify(out);
})()
"""


async def _capture(browser, tab, pre_tabs, pre_url, timeout_s=8.0):
    """Watch for new tab / same-tab nav / merchant link after a click."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        # New tab
        new_tabs = [t for t in browser.tabs if t not in pre_tabs]
        if new_tabs:
            nt = new_tabs[0]
            try:
                u = await nt.evaluate("location.href", await_promise=False)
                u = u if isinstance(u, str) else str(u)
            except Exception:
                u = ""
            if u and u != "about:blank":
                try:
                    await nt.close()
                except Exception:
                    pass
                return ("new_tab", u)
        # Same-tab nav
        try:
            cur = await tab.evaluate("location.href", await_promise=False)
            cur = cur if isinstance(cur, str) else str(cur)
        except Exception:
            cur = ""
        if cur and cur != pre_url and not cur.startswith("https://www.google.com/search?"):
            return ("same_tab", cur)
        # DOM scan for new links
        try:
            raw = await tab.evaluate(CAPTURE_JS, await_promise=False)
            data = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            data = {}
        if data.get("aclk"):
            return ("aclk_link", data["aclk"][0])
        if data.get("merchant"):
            return ("merchant_link", data["merchant"][0])
    return ("timeout", None)


async def fresh_page(browser, query):
    """Reload SERP between strategies so each strategy hits a clean card."""
    tab = browser.main_tab
    url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=shop&hl=ru&gl=ru"
    await tab.get(url)
    await asyncio.sleep(4)
    raw = await tab.evaluate(TAG_CARDS_JS, await_promise=False)
    cards = json.loads(raw) if isinstance(raw, str) else []
    return tab, cards


async def s1_element_click(browser, query):
    tab, cards = await fresh_page(browser, query)
    if not cards:
        return ("no_card", None)
    pre_tabs = list(browser.tabs)
    pre_url = await tab.evaluate("location.href", await_promise=False)
    pre_url = pre_url if isinstance(pre_url, str) else str(pre_url)
    el = await tab.select('[data-probe-idx="0"]')
    if not el:
        return ("locate_fail", None)
    try:
        await el.mouse_click()
    except Exception as exc:
        return (f"click_err:{exc}", None)
    return await _capture(browser, tab, pre_tabs, pre_url)


async def s2_cdp_mouse(browser, query):
    tab, cards = await fresh_page(browser, query)
    if not cards:
        return ("no_card", None)
    pre_tabs = list(browser.tabs)
    pre_url = await tab.evaluate("location.href", await_promise=False)
    pre_url = pre_url if isinstance(pre_url, str) else str(pre_url)
    rect_raw = await tab.evaluate(
        "(() => { const el = document.querySelector('[data-probe-idx=\"0\"]');"
        "if (!el) return JSON.stringify(null);"
        "el.scrollIntoView({block:'center'});"
        "const r = el.getBoundingClientRect();"
        "return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2}); })()",
        await_promise=False,
    )
    rect = json.loads(rect_raw) if isinstance(rect_raw, str) else None
    if not rect:
        return ("rect_fail", None)
    await asyncio.sleep(0.5)
    mb = uc.cdp.input_.MouseButton.LEFT
    await tab.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mouseMoved", x=rect["x"], y=rect["y"]))
    await asyncio.sleep(0.2)
    await tab.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mousePressed", x=rect["x"], y=rect["y"], button=mb, click_count=1))
    await asyncio.sleep(0.08)
    await tab.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mouseReleased", x=rect["x"], y=rect["y"], button=mb, click_count=1))
    return await _capture(browser, tab, pre_tabs, pre_url)


async def s3_focus_enter(browser, query):
    tab, cards = await fresh_page(browser, query)
    if not cards:
        return ("no_card", None)
    pre_tabs = list(browser.tabs)
    pre_url = await tab.evaluate("location.href", await_promise=False)
    pre_url = pre_url if isinstance(pre_url, str) else str(pre_url)
    # Make card focusable + focus it
    await tab.evaluate(
        "(() => { const el = document.querySelector('[data-probe-idx=\"0\"]');"
        "if (!el) return; el.setAttribute('tabindex','0'); el.scrollIntoView({block:'center'}); el.focus(); })()",
        await_promise=False,
    )
    await asyncio.sleep(0.3)
    await tab.send(uc.cdp.input_.dispatch_key_event(
        type_="keyDown", windows_virtual_key_code=13, key="Enter", code="Enter",
    ))
    await asyncio.sleep(0.05)
    await tab.send(uc.cdp.input_.dispatch_key_event(
        type_="keyUp", windows_virtual_key_code=13, key="Enter", code="Enter",
    ))
    return await _capture(browser, tab, pre_tabs, pre_url)


async def s4_js_click_descendants(browser, query):
    tab, cards = await fresh_page(browser, query)
    if not cards:
        return ("no_card", None)
    pre_tabs = list(browser.tabs)
    pre_url = await tab.evaluate("location.href", await_promise=False)
    pre_url = pre_url if isinstance(pre_url, str) else str(pre_url)
    # Try clicking every clickable child of the card
    await tab.evaluate(
        "(() => { const el = document.querySelector('[data-probe-idx=\"0\"]');"
        "if (!el) return;"
        "const targets = el.querySelectorAll('a, button, [role=button], [jsaction], [jsname]');"
        "for (const t of Array.from(targets).slice(0, 5)) { try { t.click(); } catch(e) {} } })()",
        await_promise=False,
    )
    return await _capture(browser, tab, pre_tabs, pre_url)


async def s5_hover_then_click_title(browser, query):
    tab, cards = await fresh_page(browser, query)
    if not cards:
        return ("no_card", None)
    pre_tabs = list(browser.tabs)
    pre_url = await tab.evaluate("location.href", await_promise=False)
    pre_url = pre_url if isinstance(pre_url, str) else str(pre_url)
    rect_raw = await tab.evaluate(
        "(() => { const el = document.querySelector('[data-probe-idx=\"0\"]');"
        "if (!el) return JSON.stringify(null);"
        "el.scrollIntoView({block:'center'});"
        "const tit = el.querySelector('[title], h3, h4, .gkQHve, .sh-np__product-title') || el;"
        "const r = tit.getBoundingClientRect();"
        "return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2}); })()",
        await_promise=False,
    )
    rect = json.loads(rect_raw) if isinstance(rect_raw, str) else None
    if not rect:
        return ("rect_fail", None)
    await asyncio.sleep(0.5)
    mb = uc.cdp.input_.MouseButton.LEFT
    await tab.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mouseMoved", x=rect["x"], y=rect["y"]))
    await asyncio.sleep(0.8)  # let hover trigger
    await tab.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mousePressed", x=rect["x"], y=rect["y"], button=mb, click_count=1))
    await asyncio.sleep(0.06)
    await tab.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mouseReleased", x=rect["x"], y=rect["y"], button=mb, click_count=1))
    return await _capture(browser, tab, pre_tabs, pre_url)


async def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    query = query_from_argv("iphone 15 купить")
    section(f"GOOGLE URL STRATEGIES — {query!r}")
    browser = await uc.start(
        headless=False, lang="ru-RU",
        user_data_dir=str(PROFILE_DIR.resolve()),
        browser_args=["--lang=ru-RU", "--window-size=1600,1000"],
    )
    results = {}
    try:
        for name, fn in [
            ("S1_element_click", s1_element_click),
            ("S2_cdp_mouse", s2_cdp_mouse),
            ("S3_focus_enter", s3_focus_enter),
            ("S4_js_click_descendants", s4_js_click_descendants),
            ("S5_hover_then_click_title", s5_hover_then_click_title),
        ]:
            info(f"trying {name}…")
            try:
                kind, url = await fn(browser, query)
            except Exception as exc:
                kind, url = f"exception:{exc}", None
            results[name] = {"kind": kind, "url": url}
            safe = (url or "").encode("ascii", errors="replace").decode("ascii")
            ok(f"  {name}: {kind} -> {safe[:80]}")
    finally:
        browser.stop()
    save_json("05_strategies", results)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
