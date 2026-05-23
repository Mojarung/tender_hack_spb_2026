"""HTTP client for the GPU reranker service (BAAI/bge-reranker-v2-m3).

Failure-mode contract:
  * ``settings.reranker_url == ""`` → disabled; ``rerank()`` returns offers unchanged.
  * timeout / 5xx / connection error → log warning + return offers in original order.

The caller (orchestrator/search.py) treats unchanged order as «no reranking applied».
"""

from __future__ import annotations

import httpx
import structlog

from pricepulse.config import get_settings
from pricepulse.domain.models import ProductOffer

log = structlog.get_logger(__name__)

# Characteristics to include first (highest discriminative value for matching).
_CHAR_PRIORITY: tuple[str, ...] = (
    "бренд", "brand", "модель", "model", "цвет", "color",
    "размер", "size", "объём", "объем", "volume", "материал", "material",
)

# Keys that add noise without helping the cross-encoder distinguish products.
_CHAR_BLACKLIST: frozenset[str] = frozenset({
    "гарантия", "страна производитель", "страна-производитель",
    "производитель", "артикул", "sku", "штрих-код", "штрихкод",
    "упаковка", "вес упаковки", "габариты упаковки",
})

_MAX_CHARS = 8


def _offer_to_doc(offer: ProductOffer) -> str:
    """Build a plain-text document from a ProductOffer for the cross-encoder."""
    parts: list[str] = [offer.name]
    chars = offer.characteristics
    seen: set[str] = set()

    for key in _CHAR_PRIORITY:
        if len(seen) >= _MAX_CHARS:
            break
        val = chars.get(key)
        if val and key not in seen:
            parts.append(f"{key}: {val}")
            seen.add(key)

    for key, val in chars.items():
        if len(seen) >= _MAX_CHARS:
            break
        if key.lower() in _CHAR_BLACKLIST or key in seen:
            continue
        parts.append(f"{key}: {val}")
        seen.add(key)

    return ". ".join(parts)


class RerankerClient:
    def __init__(
        self,
        url: str | None = None,
        timeout_s: float | None = None,
        top_n: int | None = None,
    ) -> None:
        settings = get_settings()
        url = url if url is not None else settings.reranker_url
        self._url = (url or "").rstrip("/")
        self._timeout = timeout_s if timeout_s is not None else settings.reranker_timeout_s
        self._top_n = top_n if top_n is not None else settings.reranker_top_n

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def rerank(self, query: str, offers: list[ProductOffer]) -> list[ProductOffer]:
        """Return offers sorted by rerank_score desc with rerank_score set on each.

        Falls back to the original list (unchanged) on any error.
        """
        if not self.enabled or not offers:
            return offers

        documents = [_offer_to_doc(o) for o in offers]
        payload: dict = {"query": query, "documents": documents}
        if self._top_n:
            payload["top_n"] = self._top_n

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._url}/rerank", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("reranker.unavailable", url=self._url, error=repr(exc))
            return offers

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            log.warning("reranker.bad_response", url=self._url, data=str(data)[:200])
            return offers

        # Map index → score from the reranker response.
        scores: dict[int, float] = {}
        for item in results:
            if isinstance(item, dict):
                idx = item.get("index")
                score = item.get("score")
                if isinstance(idx, int) and isinstance(score, float | int):
                    scores[idx] = float(score)

        if not scores:
            log.warning("reranker.empty_scores", url=self._url)
            return offers

        # Attach rerank_score to each offer (ProductOffer is frozen → model_copy).
        scored: list[ProductOffer] = []
        for i, offer in enumerate(offers):
            s = scores.get(i)
            scored.append(offer.model_copy(update={"rerank_score": s}))

        # Sort by score descending; offers missing a score sink to the bottom.
        scored.sort(key=lambda o: o.rerank_score or 0.0, reverse=True)
        return scored


__all__ = ["RerankerClient", "_offer_to_doc"]
