"""HTTP wrapper around SAGE FRED-T5 distilled-95M for Russian spell correction.

Replaces the previous JamSpell N-gram implementation. SAGE is a distilled
T5 transformer specifically trained by SberDevices for Russian spelling
correction (F1 = 78.9 on RUSpellRU, MIT, open weights, 383 MB on disk).

Endpoints (same contract as the old jamspell-svc):
  GET  /health   - liveness probe (returns 'loading' until the model is on device).
  POST /fix      - {"text": "..."} -> {"original": "...", "fixed": "..."}.

Methodology compliance: model is baked into the image; the running
container does no outbound calls.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import torch
from fastapi import FastAPI
from pydantic import BaseModel, Field
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

# SAGE is trained on real Russian prose, so it «оформляет» — adds trailing
# punctuation and capitalises the first letter. Marketplaces want neither,
# so we strip both back to a search-friendly query.
_TRAILING_PUNCT = re.compile(r"[.!?…]+\s*$")

logger = logging.getLogger("spellcheck-svc")
logging.basicConfig(level=logging.INFO)

# Override via SPELLCHECK_MODEL env if a different model is baked into the image.
_MODEL_ID = os.getenv("SPELLCHECK_MODEL", "ai-forever/sage-fredt5-distilled-95m")
_device: str = "cuda" if torch.cuda.is_available() else "cpu"
_tokenizer: Any = None
_model: Any = None


def _load_model() -> tuple[Any, Any]:
    logger.info("loading %s on %s", _MODEL_ID, _device)
    tok = AutoTokenizer.from_pretrained(_MODEL_ID)
    mdl = AutoModelForSeq2SeqLM.from_pretrained(_MODEL_ID).to(_device)
    mdl.eval()
    logger.info("model loaded")
    return tok, mdl


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _tokenizer, _model
    _tokenizer, _model = _load_model()
    yield


app = FastAPI(title="spellcheck-svc", lifespan=lifespan)


class FixIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class FixOut(BaseModel):
    original: str
    fixed: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok" if _model is not None else "loading"}


@app.post("/fix", response_model=FixOut)
def fix(req: FixIn) -> FixOut:
    """Sync FastAPI handler → runs in the threadpool so model.generate
    does not block the event loop."""
    if _model is None or _tokenizer is None:
        return FixOut(original=req.text, fixed=req.text)
    inputs = _tokenizer(
        req.text,
        return_tensors="pt",
        truncation=True,
        max_length=512,
    ).to(_device)
    with torch.inference_mode():
        outputs = _model.generate(
            **inputs,
            max_length=int(inputs["input_ids"].size(1) * 1.5) + 8,
            num_beams=4,
            early_stopping=True,
        )
    fixed = _tokenizer.batch_decode(outputs, skip_special_tokens=True)[0].strip()
    fixed = _TRAILING_PUNCT.sub("", fixed).strip()
    # If the caller's text was lowercase (our normalize.py always lowercases
    # before calling), match that — SAGE's title-case shouldn't leak through.
    if req.text and not req.text[:1].isupper():
        fixed = fixed.lower()
    return FixOut(original=req.text, fixed=fixed)
