# Tasks

## 2026-10-03 — Stage 4: Wire the lead interviewer

Brief: `claude files/stage-04-lead-interviewer.md`

- [ ] Approve WebSocket wire format + audio framing (blocked — waiting)
- [ ] `src/interview/server.py` — FastAPI session WS, bus + logger + transport + interviewer
- [ ] Session lifecycle: start / turns / end (`session_complete`)
- [ ] Transcript writer with truncate honouring
- [ ] Fixed opener + closer (N turns or T minutes)
- [ ] React + TS + Vite client (start, mic meter, live caption, end)
- [ ] Barge-in end-to-end (cancel generation, stop audio, truncate transcript)
- [ ] Tests: fake session, import-boundary, waterfall replay
- [ ] Exit: 3 real sessions of 5+ turns with waterfalls in `fixtures/sessions/`

## 2026-10-03 — Stage 3: Inference + TTS

Brief: `claude files/stage-03-inference-tts.md`

- [x] `LeadInterviewer` streams text, emits `draft_ready` on first sentence
- [x] `TtsAdapter` + `FakeTts` emit `tts_chunk` with word timestamps
- [x] `FakeLlm` with configurable TTFT delay + token rate
- [x] `tools/bench_ttft.py` + `tools/bench_tts.py` (mock path measured)
- [x] 20 bench transcripts in `fixtures/transcripts/bench_*.json`
- [x] Stage 3 tests (`tests/test_stage3.py`) — truncation log lines verified
- [x] Client playback-ack page: `client/stage3_playback.html`
- [x] Decision doc: `docs/decisions/inference.md` + `config/inference.yaml`
- [ ] Live TTFT/TTS benches with API keys (replace provisional vendor choice)
- [ ] Commit Stage 3 once live benches land or user accepts mock-only exit

## Done earlier

- [x] Stage 1 — event bus / JSONL log / waterfall
- [x] Stage 2 — transport, VAD, turn detection, fake STT, barge-in truncation
