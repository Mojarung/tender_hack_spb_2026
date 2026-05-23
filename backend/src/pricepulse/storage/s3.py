"""S3-compatible image cache (MinIO in dev, any S3 in prod).

Why: marketplace image CDNs (basket-XX.wbbasket.ru, Ozon CDN) periodically
re-shard URLs; caching our own copy makes the UI stable and lets us serve
images through our own CDN later. Lifecycle rule deletes objects after 30
days (configured by minio-init in docker-compose).
"""

import hashlib
from typing import TYPE_CHECKING

import aioboto3

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client


class ImageCache:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, region: str = "us-east-1") -> None:
        self._endpoint = endpoint
        self._access = access_key
        self._secret = secret_key
        self._bucket = bucket
        self._region = region
        self._session = aioboto3.Session()

    def _client(self) -> "S3Client":
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access,
            aws_secret_access_key=self._secret,
            region_name=self._region,
        )

    @staticmethod
    def _key(source: str, original_url: str) -> str:
        digest = hashlib.sha1(original_url.encode("utf-8")).hexdigest()  # noqa: S324
        return f"images/{source}/{digest[:2]}/{digest}.bin"

    async def cache(self, source: str, url: str, fetch: bytes, content_type: str = "image/webp") -> str:
        """Upload bytes to S3 and return our public URL."""
        key = self._key(source, url)
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=fetch,
                ContentType=content_type,
                CacheControl="public, max-age=2592000",
            )
        return f"{self._endpoint}/{self._bucket}/{key}"

    async def has(self, source: str, url: str) -> bool:
        key = self._key(source, url)
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False
