# Russian spell-correction microservice — SAGE FRED-T5 distilled-95M.
#
# Replaces the older JamSpell (N-gram) implementation. SAGE FRED-T5 is a
# transformer distilled by SberDevices specifically for Russian spelling
# correction (RUSpellRU F1 = 78.9, beats GPT-4 on the same benchmark).
#
# Methodology compliance: open weights (MIT), local inference, no external API.
# The model + tokenizer are baked into the image so runtime never reaches HF.
#
# Uses `uv` (ghcr.io/astral-sh/uv image) instead of plain pip — same
# package manager as the rest of the project, much faster installs.

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf-cache \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch wheel — much smaller than the GPU build.
RUN uv pip install --system --no-cache \
        --index-url https://download.pytorch.org/whl/cpu \
        'torch>=2.5,<3.0'

# Service deps — pinned loosely so security patches still land but a
# major release won't silently break the build.
RUN uv pip install --system --no-cache \
        'transformers>=4.46,<5.0' \
        'sentencepiece>=0.2,<0.3' \
        'protobuf>=5,<6' \
        'fastapi>=0.115,<1.0' \
        'uvicorn[standard]>=0.32,<1.0' \
        'pydantic>=2.9,<3.0'

# Pre-download SAGE so the container starts offline.
RUN python -c "from transformers import AutoTokenizer, AutoModelForSeq2SeqLM; \
    AutoTokenizer.from_pretrained('ai-forever/sage-fredt5-distilled-95m'); \
    AutoModelForSeq2SeqLM.from_pretrained('ai-forever/sage-fredt5-distilled-95m')"

# Lock the runtime to the cache — guarantee no HF network calls.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

WORKDIR /app
COPY server.py .

EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
