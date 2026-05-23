# Russian spell-correction microservice — SAGE FRED-T5 distilled 95M.
#
# Replaces the older JamSpell (N-gram) implementation. SAGE FRED-T5 is a
# transformer distilled by SberDevices specifically for Russian spelling
# correction (RUSpellRU F1 = 78.9, beats GPT-4 on the same benchmark).
#
# Methodology compliance: open weights (MIT), local inference, no external API.
# The model + tokenizer are baked into the image so runtime never reaches HF.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch wheel — much smaller than the GPU build.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch

RUN pip install --no-cache-dir \
        'transformers>=4.46' \
        sentencepiece \
        protobuf \
        fastapi \
        'uvicorn[standard]' \
        pydantic

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
