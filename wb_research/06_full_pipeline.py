"""06 — FULL PIPELINE: search → top-N products with chars + gallery + reviews.

PURPOSE
    Demo-shape replica of what we'd put in prod after wb_research lands.
    One query → N enriched offers. Per-offer enrichment uses (in
    parallel via asyncio.gather):

      1) basket card.json    — chars + photo_count + imt_id
      2) /feedbacks/v2/      — reviews + photo_urls + video_urls

    Plus the search response already gives nm_id, name, price, brand,
    rating, feedback_count and cover image.

    Total round-trip for limit=5 with warm DNS:
      ~250 ms search + ~300 ms enrichment in parallel = ~600 ms

USAGE
    cd wb_research
    uv run python 06_full_pipeline.py "ноутбук lenovo"

OUTPUT
    _out/<ts>_06_full_pipeline_ok.json — full enriched payload
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    WB_DEFAULT_DEST,
    WB_HEADERS,
    WB_REGIONS,
    basket_for,
    card_json_url,
    err,
    feedback_photo_urls,
    feedback_video_urls,
    feedbacks_host,
    image_url,
    info,
    ok,
    query_from_argv,
    save_json,
    section,
    warn,
)

SEARCH = "https://search.wb.ru/exactmatch/ru/common/v18/search"
LIMIT = 5
REVIEWS_PER_OFFER = 10


def _search_params(query: str) -> dict[str, str]:
    return {
        "ab_testid": "false", "appType": "1", "curr": "rub",
        "dest": str(WB_DEFAULT_DEST), "hide_dtype": "13", "lang": "ru",
        "page": "1", "query": query, "regions": WB_REGIONS,
        "resultset": "catalog", "sort": "popular", "spp": "30",
        "suppressSpellcheck": "false",
    }


def _flatten_chars(card: dict) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for grp in card.get("grouped_options") or []:
        gn = (grp.get("group_name") or "").strip()
        for opt in grp.get("options") or []:
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append((gn, name, value))
    if not out:
        for opt in card.get("options") or []:
            name = (opt.get("name") or "").strip()
            value = str(opt.get("value") or "").strip()
            if name and value:
                out.append(("", name, value))
    return out


def _enrich_review(fb: dict) -> dict:
    fb_out = dict(fb)
    photos = []
    for p in fb.get("photos") or []:
        if not p.get("isReady", True):
            continue
        key = p.get("key")
        if isinstance(key, str) and "/" in key:
            try:
                photos.append(feedback_photo_urls(key))
            except Exception:
                pass
    fb_out["photo_urls"] = photos
    v = fb.get("video")
    if isinstance(v, dict) and v.get("isReady"):
        vid = v.get("id")
        if isinstance(vid, str) and "/" in vid:
            try:
                fb_out["video_urls"] = feedback_video_urls(vid)
            except Exception:
                fb_out["video_urls"] = None
    else:
        fb_out["video_urls"] = None
    return fb_out


async def _fetch_card(client, nm_id: int):
    primary = int(basket_for(nm_id))
    for delta in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
        nn = primary + delta
        if not (1 <= nn <= 60):
            continue
        try:
            r = await client.get(card_json_url(nm_id, shard=f"{nn:02d}"))
        except Exception:
            continue
        if r.status_code == 200 and r.content:
            try:
                return r.json(), f"{nn:02d}"
            except Exception:
                continue
    return None, None


async def _fetch_reviews(client, imt_id: int):
    primary = feedbacks_host(imt_id)
    fallback = "feedbacks1.wb.ru" if primary == "feedbacks2.wb.ru" else "feedbacks2.wb.ru"
    for ver in ("v2", "v1"):
        for host in (primary, fallback):
            try:
                r = await client.get(f"https://{host}/feedbacks/{ver}/{imt_id}")
            except Exception:
                continue
            if r.status_code == 200 and r.content:
                try:
                    return r.json(), f"{host}/{ver}"
                except Exception:
                    continue
    return None, None


async def _enrich_one(client, stub: dict) -> dict:
    nm_id = stub["nm_id"]
    imt_id = stub.get("imt_id") or stub.get("root")

    card_coro = _fetch_card(client, nm_id)
    reviews_coro = _fetch_reviews(client, imt_id) if imt_id else asyncio.sleep(0, result=(None, None))
    (card, shard), (rev_body, rev_via) = await asyncio.gather(card_coro, reviews_coro)

    chars: list[tuple[str, str, str]] = []
    gallery: list[str] = []
    description = ""
    if card:
        chars = _flatten_chars(card)
        photo_count = (card.get("media") or {}).get("photo_count") or 0
        if shard and photo_count:
            gallery = [image_url(nm_id, i, shard=shard) for i in range(1, photo_count + 1)]
        description = (card.get("description") or "")[:1500]

    reviews: list[dict] = []
    total_reviews = stub.get("feedbacks") or 0
    if rev_body:
        all_fb = rev_body.get("feedbacks") or []
        reviews = [_enrich_review(f) for f in all_fb[:REVIEWS_PER_OFFER]]
        total_reviews = rev_body.get("feedbackCount") or total_reviews

    return {
        **stub,
        "shard": shard,
        "description": description,
        "characteristics": chars,
        "gallery": gallery,
        "reviews": reviews,
        "reviews_total": total_reviews,
        "reviews_via": rev_via,
    }


async def main() -> int:
    section("WB FULL PIPELINE — search + per-offer enrich (chars / gallery / reviews)")

    try:
        import httpx
    except ImportError:
        err("httpx not installed")
        return 3

    query = query_from_argv()
    info(f"query = {query!r},  limit = {LIMIT},  reviews per offer = {REVIEWS_PER_OFFER}")

    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=12) as c:
        # 1) search
        with Timer() as t_s:
            try:
                r = await c.get(SEARCH, params=_search_params(query))
            except httpx.HTTPError as exc:
                err(f"search failed: {exc}")
                return 1
        if r.status_code != 200:
            err(f"search HTTP {r.status_code}")
            return 1
        products = r.json().get("products") or (r.json().get("data") or {}).get("products") or []
        if not products:
            warn("search OK but empty")
            return 2
        info(f"search ok in {t_s.elapsed_ms} ms — {len(products)} hits, taking top {LIMIT}")

        stubs: list[dict] = []
        for p in products[:LIMIT]:
            stubs.append({
                "nm_id":   p.get("id"),
                "root":    p.get("root"),
                "imt_id":  p.get("root"),
                "name":    p.get("name"),
                "brand":   p.get("brand"),
                "supplier": p.get("supplier"),
                "price":   ((p.get("sizes") or [{}])[0].get("price", {}) or {}).get("total"),
                "rating":  p.get("nmReviewRating") or p.get("reviewRating"),
                "feedbacks": p.get("feedbacks") or p.get("nmFeedbacks") or 0,
                "url":     f"https://www.wildberries.ru/catalog/{p.get('id')}/detail.aspx",
            })

        # 2) enrich
        with Timer() as t_e:
            enriched = await asyncio.gather(*(_enrich_one(c, s) for s in stubs))

    section("Summary")
    ok(f"enriched {len(enriched)} offers in {t_e.elapsed_ms} ms (parallel)")
    for i, o in enumerate(enriched, 1):
        rating = o.get("rating") or 0
        n_chars = len(o.get("characteristics") or [])
        n_imgs = len(o.get("gallery") or [])
        n_revs = len(o.get("reviews") or [])
        total_revs = o.get("reviews_total") or 0
        price_str = f"{int(o.get('price') or 0) // 100:,} ₽".replace(",", " ")
        info(f"  [{i}] nm={o.get('nm_id')} ★{rating:.1f}  {price_str}  "
             f"{n_chars} chars, {n_imgs} imgs, {n_revs}/{total_revs} reviews")
        info(f"      {(o.get('name') or '')[:80]}")

    path = save_json("06_full_pipeline_ok", {"query": query, "offers": enriched})
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
