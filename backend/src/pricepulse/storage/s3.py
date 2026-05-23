"""S3-compatible image cache (MinIO in dev, any S3 in prod).

Why: marketplace image CDNs (basket-XX.wbbasket.ru, Ozon CDN) periodically
re-shard URLs and rate-limit hot referrers; caching our own copy makes
the UI stable and lets us serve images through our own CDN later.
The bucket has anonymous read + 30-day expiry (set by minio-init in
docker-compose), so the proxy route just 302s into the public URL.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

import aioboto3
import httpx
import structlog

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client

log = structlog.get_logger(__name__)

# Some marketplace CDNs reject empty-UA fetchers; mimic a real browser so
# WB/Ozon/Yandex serve us the same bytes the user would have downloaded.
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
}


class ImageCache:
    """Per-process image cache. One instance is held by ``api.cache``."""

    def __init__(
        self,
        endpoint: str,
        public_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        *,
        fetch_timeout_s: float = 8.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._public_url = (public_url or endpoint).rstrip("/")
        self._access = access_key
        self._secret = secret_key
        self._bucket = bucket
        self._region = region
        self._session = aioboto3.Session()
        self._fetch_timeout = fetch_timeout_s
        # In-process membership cache so we don't re-hit S3 HEAD for every
        # request to a hot image. Maps key → last-checked unix-time.
        self._known: dict[str, float] = {}

    def _client(self) -> S3Client:
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access,
            aws_secret_access_key=self._secret,
            region_name=self._region,
        )

    @staticmethod
    def _key(source: str, original_url: str) -> str:
        digest = hashlib.sha1(original_url.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"images/{source}/{digest[:2]}/{digest}"

    def public_url(self, key: str) -> str:
        return f"{self._public_url}/{self._bucket}/{key}"

    async def ensure_cached(self, source: str, url: str) -> str | None:
        """Return the public URL for the cached image, uploading it on the
        first hit. Returns ``None`` only on a hard failure (origin 4xx/5xx,
        S3 write error) — callers fall back to the original URL.
        """
        key = self._key(source, url)
        # 1) Process-local membership cache — covers warm SSR + repeat scrolls
        #    without a single S3 round-trip.
        if key in self._known:
            return self.public_url(key)

        # 2) S3 HEAD — authoritative. Costs ~1 ms against a local MinIO.
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
            except Exception:
                head_ok = False    # not cached yet — fall through to fetch + upload
            else:
                head_ok = True
        if head_ok:
            self._known[key] = time.time()
            return self.public_url(key)

        # 3) Pull from origin and upload. Both steps are best-effort.
        try:
            async with httpx.AsyncClient(
                headers=_FETCH_HEADERS, timeout=self._fetch_timeout, follow_redirects=True,
            ) as client:
                resp = await client.get(url)
            if resp.status_code != 200 or not resp.content:
                log.debug("image_cache.origin_failed", url=url, status=resp.status_code)
                return None
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            body = resp.content
        except Exception as exc:
            log.debug("image_cache.fetch_error", url=url, error=str(exc))
            return None

        try:
            async with self._client() as s3:
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    CacheControl="public, max-age=2592000",   # 30 days
                )
        except Exception as exc:
            log.warning("image_cache.s3_put_failed", url=url, error=str(exc))
            return None

        self._known[key] = time.time()
        log.info("image_cache.stored", source=source, key=key, bytes=len(body))
        return self.public_url(key)


__all__ = ["ImageCache"]
