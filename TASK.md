# Tasks

## 2026-10-06 — Stage 16: reviewable feedback, targeted practice, honest latency — DONE (browser unverified)

Baseline verified in a clean Python 3.12 venv before any change: 258 passed,
1 skipped once `python-multipart` was installed (it was imported by the intake
endpoint but never declared, so a clean install failed 25 tests). The checked-in
`.venv` is broken: a CPython 3.14 `pydantic_core` wheel was installed into a 3.12
environment. `tests/test_stage2.py` has a pre-existing wall-clock flake
(~1 in 3 runs).

Already present before this stage (not re-built): React client, FastAPI, Groq
interviewer/evaluator, Deepgram STT/TTS, sequential HR → manager → specialist
rounds, quote checks against candidate turns, reports, history, downloads,
guest isolation, concurrent round evaluation.

Checklist:

- [x] Install: declare `python-multipart`; test extras; lock file; Windows steps.
- [x] Settings: one source (env > .env > defaults); YAML no longer overrides
      `GROQ_*`/`MAX_MODEL_CALLS_PER_TURN`; `MOCK_LLM` validated; mocks refused in prod.
- [x] Groq client: SDK retries off, one retry layer, per-request timeout, overall
      deadline, single fallback hop, real model/attempt/token metadata.
- [x] Diagnostics: configured vs authenticated-request-succeeded, no secrets.
- [x] Evaluation jobs per round: queued/running/complete/failed/not_assessed,
      partial results, retry failed rounds only, cache, restart honesty, timings.
- [x] Report v3: plain claim labels, source_match ≠ validity, question+answer
      evidence, limitation, top-3 priorities; legacy reports adapted on read.
- [x] Disputes + revised assessment (labelled); excluded from progress claims.
- [x] Targeted practice: server-derived context, practice pack through the live
      runtime, coached/unaided, before/after comparison with explicit outcomes.
- [x] Intake source review (edit/exclude, provenance kept) + practice objective.
- [x] Consent, retention cleanup, deletion that cancels background work.
- [x] Room: keyboard controls, visible transcript, mic error recovery, practice timing.
- [x] Tests for all of the above; client typecheck/build; clean install check.
- [x] Evaluation set (~24) + human review sheet (unfilled).
- [x] Live provider checks + feedback latency benchmark (opt-in, billable).
- [x] Docs: README, .env.example, architecture, limitations, API/schema,
      migration, provenance template, judging map, licence inventory.
- [ ] Real-browser checks (mic, interruption, typing recovery, downloads, dispute,
      practice, reload, delete) — connected Chrome could not reach localhost.

Evidence: docs/decisions/stage16_upgrade.md.

## 2026-10-04 — Stage 15: the candidate journey, connected — DONE (browser voice unverified)

Verified: full suite green (see below); `tests/test_stage15_journey.py` drives
guest → intake → rounds interview → evaluation → report → history → plan →
delete through the real API; live Groq journey and live Deepgram voice smoke
both pass. Evidence: `docs/decisions/stage15_journey.md`. Architecture:
`docs/ARCHITECTURE.md`.

- [x] Real intake API (multipart, background, progress, recoverable failures); DOCX supported.
- [x] Guest identity, per-candidate storage, 404 isolation, full deletion.
- [x] Candidate claims with evidence kinds reach the live session; no fixture claims.
- [x] Coordinator wired: Full = HR → HM → Specialist with spoken handoffs; single rounds.
- [x] Server-reported progress; explicit voice→text fallback; provider warnings.
- [x] Background evaluation job with states, idempotency, retry; role-aware, quote-verified evaluation.
- [x] Not-assessed instead of 0.5; aggregates over assessed dimensions; delivery measured, never scored.
- [x] Results/history/plan from real data; transcript and scorecard downloads.
- [ ] In-browser voice (mic, playback, acks) not exercised in this run.
- [ ] Legacy stage-8 pipeline still present for fixture replay (not used by the app).

## 2026-10-04 — Stage 13: three distinct interviewer roles — PARTIAL

Verified: 224 passed, 1 skipped. New: `tests/test_stage13_roles.py` (47 tests).

Done:

- [x] `session/roles.py` — HR, Hiring Manager, Domain Specialist as separate
      specs: disjoint competencies, own depth caps (2/3/4), own rubric ids, own
      voices, own model instructions, own probe templates.
- [x] Per-role anchor extraction. The same answer yields three different spans
      (career motive / owned decision / thing built), so the three roles ask
      three genuinely different follow-ups. Proven by
      `test_same_answer_produces_three_different_follow_ups`.
- [x] Nine role packs as data: `hr-core`, `manager-core`, and
      `specialist-{software,hardware,sales,marketing,operations,finance,generic}`.
      Each specialist pack asks profession-specific questions — verified all six
      produce distinct text.
- [x] Pack schema extended with a versioned `rubric` block. Legacy packs keep
      the four-dimension weights and still load.
- [x] `session/interview_config.py` — validated role/seniority/round/lane/
      context config. Background required; **repository optional everywhere**.
      Generic coverage is a labelled choice, not a silent software fallback.
- [x] `session/coordinator.py` — sequential rounds, normalised time allocation,
      source-linked handoffs carrying statements + open questions and
      **refusing** to carry scores/ratings/impressions.
- [x] `evaluation/rubrics.py` — level-based assessment replacing the
      support-count ratio. `insufficient_evidence` is not a low score, scored
      dimensions must cite evidence, unverifiable quotes downgrade the
      judgement, weights renormalise over assessed dimensions only.
- [x] `ModelProposer` — one bounded model call for the **active round only**,
      role instructions + delimited candidate data, validated structured
      proposal; malformed replies fall back to a real question and are counted.
- [x] Fixed `depth_on_current < 1`, which capped every follow-up at one level
      regardless of pack or intensity.
- [x] Fixed single-round time allocation (an HR-only interview was getting 25%
      of the budget and would have ended a quarter of the way in).
- [x] CLAUDE.md "three live personas" restriction narrowed: contention is still
      banned, sequential rounds sharing one guard are sanctioned.

Not done — the workflow is not yet connected end to end:

- [ ] `runtime.py` / `panel.py` still run the old single `propose()`; the
      coordinator and proposers are not wired into the live session.
- [ ] `server.py` has no interview-config endpoint and no intake wiring; the
      frontend upload still does not reach intake.
- [ ] Evaluation pipeline does not yet emit `RoundScore`s; the dashboard still
      reads fixture `dashboard.json`.
- [ ] Frontend setup has no role/seniority/round selection; the interview room
      does not display the active round or transitions.
- [ ] Deepgram adapters (stage 12) still not wired; `FakeSpeakPort` is injected.
- [ ] No live provider validation of any kind.

## 2026-10-04 — Stage 12: real voice providers (Deepgram) — PARTIAL

Evidence: `docs/decisions/stage12_voice_providers.md`

Done and verified offline (177 passed, 1 skipped):

- [x] Provider contracts verified against official docs before coding. Seven
      prior assumptions were wrong — see the table in the decision doc.
- [x] `src/interview/config.py` — one validated settings object. Secrets are
      `SecretStr`; retired models, non-PCM encodings, non-Aura voices and
      `APP_ENV=prod` + mocks all fail at startup.
- [x] Retired `gemma2-9b-it`; fallbacks are `openai/gpt-oss-20b` / `-120b`.
- [x] `src/interview/transport/audio.py` — stateful PCM conversion/resampling
      that is correct across chunk seams (verified spectrally).
- [x] `src/interview/transport/deepgram_stt.py` — `/v1/listen` over websockets:
      `keyterm` (not `keywords`), explicit format declaration, `vad_events`,
      keepalive, and **exactly one answer per turn** from buffered `is_final`
      segments closed by `speech_final` / `UtteranceEnd` / client finalise.
- [x] `src/interview/transport/deepgram_tts.py` — `/v1/speak` over websockets:
      sentence-wise `Speak`, one `Flush` per line under the documented 20/min
      limit, `Clear` + late-chunk rejection, per-persona Aura-2 voices, and
      word timings honestly marked estimated.
- [x] Additive schema fields: transcript `confidence`, turn `boundary`,
      `timings_estimated`, TTS `sample_rate`/`encoding`, ack `scheduled_ms`.
      Stage-1 replay fidelity still passes.
- [x] `pypdf` replaces the regex PDF extractor, which returned empty text for
      every compressed (i.e. normal) PDF and silently accepted a blank profile.
      Scanned, encrypted and corrupt PDFs now fail with distinct recovery paths.
- [x] Removed the unused `deepgram-sdk` dependency — it was declared but never
      installed, and the adapter caught `ImportError` and ran as a no-op.
- [x] `.env.example` rewritten: every variable, placeholders only.

Not done — do not read the above as a working voice interview:

- [ ] Wire the adapters into `server.py` (still injects `FakeSpeakPort`) and
      browser mic capture into the authenticated session socket.
- [ ] Client playback accounting from the audio clock; queue teardown on
      barge-in; populate `scheduled_ms`. The schema field exists; nothing
      writes it.
- [ ] Push-to-talk must gate capture/transcription, not just interruption.
- [ ] Authentication, ownership checks, persistence, post-session evaluation
      runner, real dashboard APIs (items 1, 7, 9).
- [ ] Model-driven interviewer follow-ups through the guard (item 4).
- [ ] Rubric-based evaluation replacing the support-count ratio (item 6).
- [ ] Language-aware repository exploration beyond README/Python (item 5).
- [ ] **All live validation.** No `DEEPGRAM_API_KEY` or `GROQ_API_KEY` in this
      environment. No latency measurement has been taken; the ~450 ms target
      remains unverified and the existing waterfall numbers are mock-path only.

## 2026-10-04 — Stage 11: Panel mode, hardening and lanes

Brief: `docs/stages/stage-11-panel-hardening-lanes.md`
Evidence: `docs/decisions/stage11_panel.md`

- [x] Panel mode behind `PANEL_MODE`: 2–3 interviewer agents sharing one guard,
      one voice at a time, distinct TTS voices from the pack roster
- [x] `persona` filled on `likely_next`, `floor_granted`, `draft_ready`, `agent_step`
      (additive, schema still v2; v1/early-v2 logs replay field-for-field unchanged)
- [x] One evaluation per panel session, not one per persona
- [x] Fallbacks, each exercised once and logged as `fallback_used`: provider
      failover, push-to-talk, resume-only, fallback repo, text lane, WS reconnect
- [x] Text lane — same bus, agent, guard and evaluation; delivery "not assessed"
      and an unassessed dimension is not stored as a zero
- [x] Optional video — local client preview only; no server ingest path, and
      contract-9 enforcement by test
- [x] Hardening: WS reconnect (one log across a drop), session cap, intake
      rate limit, delete-my-data with a manifest
- [x] Recorded `fixtures/sessions/stage11_{panel,text_lane,fallbacks}/`
- [x] `tests/test_stage11_server.py` — the composition root driven through a
      real WebSocket (this is what caught the reconnect subscription leak and
      the disconnect-as-message bug)
- [x] Live coding pack — **proposal only**, not built:
      `docs/decisions/stage11_live_coding_proposal.md`
- [ ] Live-vendor panel waterfall (the 1.2 s p50 exit criterion). Mock path
      only: p50 0.5 ms, which measures the panel machinery, not the vendor legs.
      `GROQ_API_KEY` is unset and no TTS vendor is configured, so the vendor call
      was not run and no sleep was added to fake it. Same gap as stage 7.

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
