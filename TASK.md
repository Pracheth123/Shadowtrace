# Tasks

## 2026-10-03 — Stage 4: Wire the live interviewer

Brief: `docs/stages/stage-04-lead-interviewer.md`

- [x] Agentic design docs + schema v2 (prerequisite override)
- [x] `src/interview/server.py` — FastAPI session WS composition root
- [x] `LiveSession` lifecycle + transcript writer + opener/closer
- [x] React + TS + Vite client (start, mic meter, caption, end, barge-in)
- [x] Tests: fake session, import-boundary, truncate
- [x] Three recorded sessions (5 turns) + waterfalls in `fixtures/sessions/stage4_sess_*`
- [x] FakeStt composition via `FAKE_STT_PATH` (browser `candidate_final` remains)
- [ ] Live mic + vendor STT/LLM sessions (optional; needs API keys) for 1.1–1.5 s real baseline

## 2026-10-03 — Groq model client

- [x] `interview.llm.GroqModelClient` (OpenAI SDK → Groq base_url, `.env` key)
- [x] Per-role models in `config/inference.yaml` (TTFT + tool-calling)
- [x] Shared RPM limiter, 429 backoff, per-turn cap, `model_call` events
- [x] Unit tests fake-only; `@pytest.mark.live` skipped by default
- [ ] Re-run `bench_ttft.py` against Groq to replace provisional role models

## 2026-10-03 — Stage 3: Inference + TTS

- [x] Offline inference/TTS path + benches (mock)
- [ ] Live TTFT benches via Groq (`pytest -m live` / bench script)

## Done earlier

- [x] Stage 1 — event bus / JSONL log / waterfall (+ schema v2 agent events)
- [x] Stage 2 — transport, VAD, turn detection, fake STT, barge-in truncation
