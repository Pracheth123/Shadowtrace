# Tasks

## 2026-10-03 — Stage 10: Intensity and confidence guardrail

Brief: `docs/stages/stage-10-intensity-guardrail.md`

- [x] Intensity config `coach` / `realistic` / `panel` (panel is depth only; no second voice)
- [x] Guard and agent read intensity for probe depth, hints, and interruption
- [x] Distress steps down one level once; two steady turns step back once; spine still asked
- [x] Tone guardrail on every spoken line
- [x] Client hardness picker sent on `session_start`
- [x] Report notes intensity and does not change scores for it
- [x] Distress replay and coach-vs-realistic evidence in `docs/decisions/stage10_intensity.md`

## 2026-10-03 — Stage 9: Roadmap, public pack, dashboard

Brief: `docs/stages/stage-09-roadmap-pack-dashboard.md`

- [x] SQLite longitudinal store, append-only write path
- [x] Trends compare sessions of one pack only
- [x] Roadmap agent: list sessions, scores, claim history, gaps; 3–5 prep items with evidence
- [x] `public-company` pack from public themes, original spine text
- [x] React dashboard at `#dashboard`, first card from `fake_eval`
- [x] Seed of 4 sessions, trend, and roadmap in `docs/decisions/stage9_store.md`

## 2026-10-03 — Stage 8: Evaluation pass

Brief: `docs/stages/stage-08-evaluation-pass.md`

- [x] Offline pipeline: transcript, event log, claims file; resume/repo text only as delimited data
- [x] Pattern detectors with no model call
- [x] Three evaluator agents (substance, structure, delivery) with lookup tools and an eval trace file
- [x] Claim adjudication with a negation check (`held` / `collapsed` / `untested`)
- [x] Scorer reads findings only
- [x] Report JSON + HTML, `fake_eval`, CLI `tools/evaluate.py`
- [x] Transcript PDF linked from the report, written without a new library
- [x] Stage-4 recordings and two hand-written transcripts in `docs/decisions/stage8_evaluation.md`

## 2026-10-03 — Stage 7: Signal bus, turn controller, speculative loop

Brief: `docs/stages/stage-07-signal-bus-turn-controller.md`

- [x] `signals.py` — claim hits, hedge vs own baseline, distress; under 50 ms/partial
- [x] `turn_controller.py` — `likely_next` on a stable complete partial, `floor_granted` at endpoint
- [x] Speculative `LiveAgent.run(commit=False)` while the candidate talks; commit and speak after the final
- [x] Router: defended / conceded / unclear; one local refresh when the draft is stale
- [x] `note_claim_status` logged as a tool result; scorer view is transcript text only
- [x] Crowd-noise frames do not reach barge-in confidence
- [x] Recorded `fixtures/sessions/stage7_speculative/` + `docs/decisions/stage7_waterfall.md`
- [ ] Live-vendor waterfall (the ~450 ms / ~1.0 s budget). Mock path only: p50 0.2 ms, stale-draft rate 0.20. `GROQ_API_KEY` is unset and there is no `.env`, so the vendor call was not run and no sleep was added to fake it.

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
