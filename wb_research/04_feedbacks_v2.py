"""04 — reviews via /feedbacks/v2/{imt_id} with photos + video.

PURPOSE
    Prod `wb_feedbacks.py` uses v1 which doesn't expose the rich
    photos[]/video{} blocks. Live-verified May 2026: v2 returns the
    same payload as v1 PLUS:

      • photos: [{id, key:"{shard}/{uuid}", isBlurred, isReady}, ...]
      • video:  {id:"{shard}/{uuid}", durationSec, isReady}
      • nmValuationDistribution: per-nm rating histogram
      • feedbackCount{,WithPhoto,WithText,WithVideo}: real totals
      • valuationDistribution{,Percent}: overall 1..5 histogram

    Photo URLs are reconstructed from `key`:
        https://feedback-{shard:02d}.wbbasket.ru/{uuid}/ms.webp
        https://feedback-{shard:02d}.wbbasket.ru/{uuid}/fs.webp

    Video URLs are HLS (no single .mp4):
        https://videofeedback{shard:02d}.wbbasket.ru/{uuid}/index.m3u8
        + preview.webp

USAGE
    cd wb_research
    uv run python 04_feedbacks_v2.py <imt_id> [limit=10]
    # Get imt_id from 01 (`root` field) or 03 output.

EXIT CODES
    0 — got >=1 review
    1 — both shards 4xx
    2 — 200 but feedbacks[] empty
    3 — network/import error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (
    Timer,
    WB_HEADERS,
    err,
    feedback_photo_urls,
    feedback_video_urls,
    feedbacks_host,
    info,
    ok,
    save_json,
    section,
    warn,
)


def _enrich_one(fb: dict) -> dict:
    """Add photo_urls and video_urls computed from feedback `key`/`id`."""
    fb_out = dict(fb)
    photos: list[dict[str, str]] = []
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
    else:
        fb_out["video_urls"] = None
    return fb_out


async def main() -> int:
    section("WB FEEDBACKS V2 — reviews with photos + video URLs")

    if len(sys.argv) < 2:
        err("usage: uv run python 04_feedbacks_v2.py <imt_id> [limit=10]")
        return 3
    try:
        imt_id = int(sys.argv[1])
    except ValueError:
        err(f"imt_id must be int; got {sys.argv[1]!r}")
        return 3
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    import httpx

    primary = feedbacks_host(imt_id)
    fallback = "feedbacks1.wb.ru" if primary == "feedbacks2.wb.ru" else "feedbacks2.wb.ru"
    info(f"imt_id   = {imt_id}")
    info(f"primary  = {primary}  (CRC-16/ARC picker)")
    info(f"fallback = {fallback}")
    info(f"limit    = {limit}")

    body: dict | None = None
    used_host = None
    used_version = None
    async with httpx.AsyncClient(http2=True, headers=WB_HEADERS, timeout=12) as c:
        for ver in ("v2", "v1"):
            for host in (primary, fallback):
                url = f"https://{host}/feedbacks/{ver}/{imt_id}"
                with Timer() as t:
                    try:
                        r = await c.get(url)
                    except httpx.HTTPError as exc:
                        info(f"  {ver} {host}: network error ({exc})")
                        continue
                info(f"  {ver} {host}: HTTP {r.status_code}  ({t.elapsed_ms} ms, {len(r.content)} bytes)")
                if r.status_code == 200 and r.content:
                    try:
                        body = r.json()
                    except Exception as exc:
                        info(f"    parse error: {exc}")
                        continue
                    used_host = host
                    used_version = ver
                    break
            if body is not None:
                break

    if body is None:
        err("every feedbacks endpoint returned non-200 or invalid JSON")
        return 1

    feedbacks = body.get("feedbacks") or []
    if not feedbacks:
        warn("body parsed but feedbacks[] empty — product likely has no reviews yet")
        save_json("04_no_reviews", {"used_host": used_host, "version": used_version, "body": body})
        return 2

    enriched = [_enrich_one(fb) for fb in feedbacks][:limit]

    section("Summary")
    ok(f"got {len(enriched)} reviews via {used_host} ({used_version})")
    info(f"total on imt:     {body.get('feedbackCount')}")
    info(f"with photo:       {body.get('feedbackCountWithPhoto')}")
    info(f"with video:       {body.get('feedbackCountWithVideo')}")
    info(f"avg valuation:    {body.get('valuation')}")
    info(f"distribution:     {body.get('valuationDistribution')}")
    print()
    for i, fb in enumerate(enriched[:5], 1):
        rating = fb.get("productValuation")
        author = (fb.get("wbUserDetails") or {}).get("name") or "Аноним"
        text = (fb.get("text") or "")[:80]
        nphotos = len(fb.get("photo_urls") or [])
        has_vid = bool(fb.get("video_urls"))
        info(f"  [{i}] ★{rating} {author}: {text!r}  {nphotos} photos, video={has_vid}")

    path = save_json("04_reviews_ok", {
        "imt_id": imt_id, "used_host": used_host, "version": used_version,
        "total": body.get("feedbackCount"),
        "with_photo": body.get("feedbackCountWithPhoto"),
        "with_video": body.get("feedbackCountWithVideo"),
        "valuation": body.get("valuation"),
        "valuation_distribution": body.get("valuationDistribution"),
        "reviews": enriched,
    })
    ok(f"saved → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
