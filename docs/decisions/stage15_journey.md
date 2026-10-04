# Stage 15 — connecting the candidate journey

Date: 2026-10-04. Goal: candidate context → intake → personalised interview →
final transcript → evaluation → real results → next practice, reachable from
the browser, with data from the candidate's own session.

## Gap status

| Gap (from the review) | Status | Where |
|---|---|---|
| Setup stored resume/name/company locally only | **Fixed** — multipart `POST /api/intake`; every visible field changes the workflow; unused "name" field removed | `SetupPage.tsx`, `server.py` |
| `session_start` carried only lane/intensity/panel/pack | **Fixed** — carries `auth_token` + `intake_id`; config comes from the stored intake | `use-session.ts`, `server.py` |
| No role/family/seniority/round/repo/JD inputs | **Fixed** | `SetupPage.tsx` |
| `/intake` acknowledged without running intake | **Fixed** — stub removed; real background pipeline with progress | `services/intake.py` |
| Intake only via `tools/intake.py` | **Fixed** (CLI kept for offline use) | |
| Browser sessions could load fixture claims | **Fixed** — server always passes the intake's claims (or none); fixture only for bare unit tests | `runtime.py`, `server.py` |
| Coordinator not used by browser sessions | **Fixed** — rounds mode | `runtime.py` |
| Completion didn't trigger evaluation | **Fixed** — idempotent background job with states + retry | `services/evaluation.py` |
| Dashboard read `/dashboard.json` | **Fixed** — file deleted; reports fetched by session id | `ResultsDashboard.tsx` |
| History "View feedback" showed a toast | **Fixed** — opens `#results/<sid>` | |
| Frontend accepted DOCX, backend rejected it | **Fixed** — DOCX extracted (stdlib), one shared type list, content checks both sides | `intake/documents.py` |
| Hardcoded "10 questions" | **Fixed** — server progress, core vs follow-ups, per round | `progress_payload` |
| Word-count/cue/overlap/negation assessment | **Fixed for browser sessions** — role-aware model evaluation with verified quotes. The old stage-8 pipeline (`evaluation/pipeline.py`, `tools/evaluate.py`) is still in the repo for replaying legacy fixtures and is not used by the app | `role_eval.py` |
| Default 0.5 with no evidence | **Fixed in the app path** (`insufficient_evidence`, no score). Legacy `evaluation/score.py` still returns 0.5 and is only used by the legacy pipeline | |
| Substance/structure/delivery passes instead of role-aware | **Fixed** — HR / hiring manager / domain specialist perspectives per round rubric | |
| Store/roadmap not consumed by browser | **Fixed** — `ReportStore` + `/api/history` + `/api/plan`. The stage-9 roadmap *agent* is not used; the plan is deterministic and evidence-linked | `services/history.py` |
| Docs/pitch/code describe different stages | **Partially fixed** — `docs/ARCHITECTURE.md` documents the implemented system; the pitch deck was not edited (not in the repo) | |
| E2B / live code execution | **Still open, by design** — not implemented, documented as absent | |
| Browser microphone/playback in a real page | **Unverified in this run** — see below | |

## Evidence: one real session (Groq interviewer + Groq evaluator)

`python tools/live_journey_smoke.py --out logs/live_journey` (output kept in
`logs/live_journey/evidence.json`, git-ignored). Session
`1ab1f9cd-8afa-466e-9444-792dae373042`, intake `9bb5bfea…`, evaluator
`groq / qwen/qwen3.8-27b`, `is_assessment: true`.

**Candidate input → claims** (all `candidate_assertion`, verbatim from the resume):

- c-resume-1 "I led the migration of our card settlement reconciliation from nightly batch jobs to a streaming pipeline on Kafka."
- c-resume-2 "I chose idempotent consumers keyed on settlement id because duplicate bank files were our biggest source of mismatches."
- c-resume-3 "I reduced reconciliation mismatches from about 2 percent to under 0.1 percent over one quarter."

**Interview** (software specialist round; the model's follow-ups use the actual answers):

- Spine: "Take a system you built. Walk me through the design and the choice you were least sure about."
- After the answer about moving batch → Kafka: "You moved from nightly batch to streaming via Kafka for settlement files. What was the alternative you rejected, and what specific tradeoff … made Kafka the right call…?"
- After the answer about replaying a month of files: "When you replayed a month of real files, did you run that against a production replica or a staging Postgres?…"

**Evaluation → displayed report** (specialist.software.v1, 13.1 s, 1 attempt, 0 rejected quotes):

| Dimension (weight) | Level | Verified quote (turn) |
|---|---|---|
| Technical substance (40%) | solid | "I proposed streaming settlement files through Kafka, wrote the design doc, and got sign-off from fin…" (00a31b11) |
| Implementation and debugging (35%) | solid | "I profiled it and found one hot partition for a large merchant, so we re-keyed by merchant…" (2bad7b88) |
| Design tradeoffs (25%) | solid | "We rejected exactly-once delivery in Kafka because it tied us to transactional producers…" (862357f2) |

Round score 0.65 from weights {0.40, 0.35, 0.25} over 3 of 3 assessed
dimensions. Gaps found by the evaluator were accurate for the scripted answers
(e.g. "did not answer the follow-up regarding production vs staging").
Claims: c-resume-1 **held**, c-resume-2 **held** (quotes verified),
c-resume-3 **untested** (never discussed). Limitations recorded: "You ended
the interview early", "3 of 4 core questions were asked".

## Evidence: real voice providers

`python tools/live_voice_smoke.py --yes` — all PASS: Deepgram Aura-2 TTS (first
audio byte 602 ms, linear16 @ 24 kHz), resample 24→16 kHz into Deepgram nova-3
(94% word overlap, one committed answer, boundary `speech_final`), Groq
follow-up referencing "outreach"/"budget", barge-in `Clear` sent and 14 late
chunks dropped.

## Not verified

- **The browser half of voice**: microphone capture through the AudioWorklet,
  playback scheduling, audio-clock acknowledgements and the start-gesture
  unlock. The Claude-in-Chrome session could not reach the dev server
  (`localhost:5173` showed a browser error page while `curl` returned 200), so
  no in-browser run happened. The client typechecks and builds; these paths
  need a manual run with a microphone.
- **The full UI flow in a browser** for the same reason; it is covered at the
  API level by `tests/test_stage15_journey.py`, which uses the same endpoints
  and WebSocket messages as the client.
