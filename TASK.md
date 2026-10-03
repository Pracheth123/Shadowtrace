# Tasks

## 2026-10-03 — Stage 4: Wire the live interviewer

Brief: `docs/stages/stage-04-lead-interviewer.md`

- [x] Agentic design docs + schema v2 (prerequisite override)
- [x] Approve wire format (owner: proceed without further gates)
- [x] `src/interview/server.py` — FastAPI session WS composition root
- [x] `LiveSession` lifecycle + transcript writer + opener/closer
- [x] React + TS + Vite client (start, mic meter, caption, end, barge-in)
- [x] Tests: fake session, import-boundary, truncate
- [ ] Three real recorded sessions + waterfalls in `fixtures/sessions/`
- [ ] Compose transport STT into server (currently `candidate_final` / Web Speech lane)

## 2026-10-03 — Stage 3: Inference + TTS

Brief: `claude files/stage-03-inference-tts.md`

- [x] Offline inference/TTS path + benches (mock)
- [ ] Live TTFT/TTS benches with API keys

## Done earlier

- [x] Stage 1 — event bus / JSONL log / waterfall (+ schema v2 agent events)
- [x] Stage 2 — transport, VAD, turn detection, fake STT, barge-in truncation
