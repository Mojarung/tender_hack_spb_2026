"""HTTP wrapper around the JamSpell Russian spell corrector.

Loads the first ``*.bin`` model it finds under ``/opt/model`` at startup
(the Dockerfile downloads ``ru.tar.gz`` from JamSpell-models, which
contains the canonical Russian model — file name varies between releases,
so we glob).

Endpoints:
  GET  /health   → liveness probe.
  POST /fix      → ``{"text": "..."} → {"original": "...", "fixed": "..."}``.

Methodology compliance: runs on the team's docker host, no outbound
calls, no third-party APIs.
"""

from __future__ import annotations

import glob
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jamspell
from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger("jamspell-svc")
logging.basicConfig(level=logging.INFO)

_MODEL_DIR = "/opt/model"
_corrector: Any = None


def _load_corrector() -> Any:
    candidates = sorted(glob.glob(os.path.join(_MODEL_DIR, "*.bin")))
    if not candidates:
        raise RuntimeError(f"No JamSpell .bin model found under {_MODEL_DIR}")
    model_path = candidates[0]
    logger.info("loading JamSpell model: %s", model_path)
    c = jamspell.TSpellCorrector()
    if not c.LoadLangModel(model_path):
        raise RuntimeError(f"LoadLangModel failed for {model_path}")
    logger.info("JamSpell ready")
    return c


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _corrector
    _corrector = _load_corrector()
    yield


app = FastAPI(title="jamspell-svc", lifespan=lifespan)


class FixIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class FixOut(BaseModel):
    original: str
    fixed: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok" if _corrector is not None else "loading"}


@app.post("/fix", response_model=FixOut)
def fix(req: FixIn) -> FixOut:
    """Sync FastAPI handler → runs in the threadpool so the event loop is
    not blocked by JamSpell's C++ call."""
    if _corrector is None:
        return FixOut(original=req.text, fixed=req.text)
    fixed = _corrector.FixFragment(req.text)
    return FixOut(original=req.text, fixed=str(fixed))
