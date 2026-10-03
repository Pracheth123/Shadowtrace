# Tasks

## 2026-10-03 — Stage 7: Signal bus, turn controller, speculative loop

Brief: `docs/stages/stage-07-signal-bus-turn-controller.md`

- [x] `signals.py` — claim hits, hedge vs own baseline, distress; under 50 ms/partial
- [x] `turn_controller.py` — `likely_next` on a stable complete partial, `floor_granted` at endpoint
- [x] Speculative `LiveAgent.run(commit=False)` while the candidate talks; commit and speak after the final
- [x] Router: defended / conceded / unclear; one local refresh when the draft is stale
- [x] `note_claim_status` logged as a tool result; scorer view is transcript text only
- [x] Crowd-noise frames do not reach barge-in confidence
- [x] Recorded `fixtures/sessions/stage7_speculative/` + `docs/decisions/stage7_waterfall.md`
- [ ] Live-vendor waterfall (the ~450 ms / ~1.0 s budget). This recording is the mock path: p50 0.2 ms, stale-draft rate 0.20

## 2026-10-03 — Stage 6: Intake, indexer, fit

Brief: `docs/stages/stage-06-intake-indexer-fit.md`

- [x] Intake schemas (`IntakeRequest`, `ResumeProfile`, `Claim`, `ClaimsFile`, `FitGap`)
- [x] Resume parser adapted from skill-sync sections, cap, and local skill overlap; instruction lines stripped
- [x] Read-only indexer (`list_dir`, `read_file`, `grep`, `git_log`), step limit, exploration JSONL
- [x] Claims file capped at 8; text must appear in the resume or a file that was read
- [x] Rule-based fit; classifier only for unsure pairs
- [x] Fallbacks: resume-only and `--repo-path`; CLI `tools/intake.py`
- [x] Public run: `fixtures/intake/stage6_public/` (`docs/decisions/stage6_intake.md`)
- [ ] Session `claims_path` is optional; the default live session still uses the stage-5 fixture until a session is pointed at `claims.json`

## 2026-10-03 — Stage 5: Guard and spine/probe split

Brief: `docs/stages/stage-05-planner-spine.md`

- [x] Pack schema + `behavioral-core` and `systems-design` YAML
- [x] In-memory tools (`get_claims`, `get_claim`, `get_coverage`, `get_time_remaining`, `get_candidate_signals`, `note_claim_status`, `ask_spine`, `end_session`)
- [x] Pure guard (spine order/verbatim, time budget, probe depth, claims scope)
- [x] No repeated spine or probe in a session, including a near-duplicate (`question_repeat`)
- [x] Minimal asyncio agent loop with step limit; `question_planned` / `coverage_update` after acceptance
- [x] Tests: guard table, logged overrides, identical spine text, replay without live tools
- [x] Recorded sessions `fixtures/sessions/stage5_sess_{a,b,live}` + `docs/decisions/stage5_guard.md`
- [ ] Stage 6 replaces `fixtures/claims/stage5_claims.json` with a real Claims File

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
