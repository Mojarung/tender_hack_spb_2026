"""HTTP client for the local spellcheck-svc microservice.

The service runs SAGE FRED-T5 distilled-95M (Russian spelling correction)
in ``backend/spellcheck/`` on the docker network — no external APIs.

Failure-mode contract:
  * ``settings.spellcheck_url == ""`` → disabled; every ``fix()`` returns ``None``.
  * service unreachable / 5xx / malformed reply → returns ``None``.

The caller (`enrichment/normalize.py`) treats ``None`` as «no correction
was applied» and proceeds — a flaky spellcheck never breaks search.
"""

from __future__ import annotations

import httpx
import structlog

from pricepulse.config import get_settings

log = structlog.get_logger(__name__)


class SpellCheckClient:
    def __init__(self, url: str | None = None, timeout_s: float = 5.0) -> None:
        url = url if url is not None else get_settings().spellcheck_url
        self._url = (url or "").rstrip("/")
        self._timeout = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def fix(self, text: str) -> str | None:
        """Return spell-corrected text, or ``None`` when no correction is
        available (client disabled, service down, response malformed)."""
        if not self.enabled or not text.strip():
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._url}/fix", json={"text": text},
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.debug("spellcheck.unavailable", url=self._url)
            return None
        fixed = data.get("fixed") if isinstance(data, dict) else None
        return fixed if isinstance(fixed, str) else None


__all__ = ["SpellCheckClient"]
