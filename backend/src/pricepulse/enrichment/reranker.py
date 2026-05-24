"""Optional semantic reranker client.

The reranker is intentionally a late post-rank layer: exact structured
attribute matching still happens before it and hard mismatches stay hard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class RerankResult:
    index: int
    score: float


class RerankerProtocol(Protocol):
    async def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[RerankResult]:
        """Return rerank scores keyed by original document index."""


class HttpReranker:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 8.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[RerankResult]:
        if not query.strip() or not documents:
            return []
        payload: dict[str, Any] = {"query": query, "documents": documents}
        if top_n is not None:
            payload["top_n"] = top_n

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(f"{self._base_url}/rerank", json=payload)
            response.raise_for_status()
            data = response.json()

        raw_results = data.get("results") if isinstance(data, dict) else data
        if not isinstance(raw_results, list):
            return []

        results: list[RerankResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["index"])
                score = float(item["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(documents):
                results.append(RerankResult(index=index, score=score))
        return results


__all__ = ["HttpReranker", "RerankResult", "RerankerProtocol"]
