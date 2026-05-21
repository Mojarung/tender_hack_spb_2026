# BPMN

Sketch the search flow at https://bpmn.io/ and export SVG to `docs/bpmn.svg`
before the defense (24.05.2026, 13:00).

High-level lanes:

- **User** → submits query.
- **API gateway** → normalize (typo, synonyms).
- **Orchestrator** → fan-out to N adapters (parallel).
- **Adapters** (WB / Ozon / Ya.Market / Runet) → per-source flow:
  - check cache → if hit, return.
  - check rate-limit → if exceeded, defer via arq.
  - request source (httpx or stealth browser).
  - on CAPTCHA → solver subprocess.
  - parse offers → push to stream.
- **Aggregator** → group by source, compute min/median, emit `done`.
