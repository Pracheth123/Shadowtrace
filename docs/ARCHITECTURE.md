# Shadow Trace — implemented architecture (stage 16)

This describes what the code does today, end to end. Where the pitch deck,
older stage briefs or `PLANNING.md` disagree with this file, this file and the
code are current. Optional features that do **not** exist are listed at the end.

Stage 16 additions are marked **(16)**. What changed and why, with evidence:
`docs/decisions/stage16_upgrade.md`. API and report schema: `docs/API.md`.

## The journey, as a candidate reaches it

```
Browser (React)                     Server (FastAPI, one process)                  Disk
───────────────                     ─────────────────────────────                  ────
Setup form ──POST /api/guest──────▶ guest identity (token hash only) ───────────▶ data/candidates/<cid>/identity.json
           ──POST /api/intake─────▶ validate InterviewConfig + uploads
                                    background job (services/intake.py):
                                      read documents (PDF/DOCX/TXT/MD)
                                      optional repo: bounded read-only clone,
                                        README + manifests, clone deleted
                                      claims (verbatim spans, evidence kind)
                                      fit/gap vs job description
                                      preparation guidance (with evidence) ──────▶ …/intake/<iid>/{config,claims,fit_gap,
Progress  ◀─GET /api/intake/<iid>── status.json stages                                prep,sources,status}.json
Review prep, Start
Interview room ──WS session_start{auth_token, intake_id}──▶ LiveSession, rounds mode
                                      Coordinator: HR → Hiring manager → Specialist
                                      per round: SessionTools(pack spine, slice) +
                                        LiveAgent(RoleSpec, proposer) under the guard
                                      handoff brief (statements + open questions)
                                      progress / round_transition on the wire ───▶ …/sessions/<sid>/{session.jsonl,
                                    session closes → transcript + log flushed            transcript.json, meta.json}
                                    → EvaluationService.enqueue (after band 2 ends)
Results  ◀─GET /api/sessions/<sid>── meta.state: finalising → evaluation_queued →
  (poll)                              evaluating → complete | failed (retryable)
         ◀─GET …/report────────────── role-aware evaluation (evaluation/role_eval.py) ▶ …/evaluation/{report.json,scorecard.html}
         ◀─GET …/scorecard, …/transcript   downloads                                    data/reports.sqlite (ReportStore)
History  ◀─GET /api/history───────── compatible comparisons, recurring gaps
Plan     ◀─GET /api/plan──────────── recurring gaps + latest recommendations + intake prep
Delete   ──POST /api/me/delete──────▶ erase candidate dir, report rows, legacy rows, identity
```

### Stage 16 additions to the journey

```
Setup ── consent (Groq, Deepgram, guest key, retention) ──POST /api/intake{consent=1}
Review ── statements shown inside their source text ──PUT /api/intake/<iid>/review
          keep | correct meaning (stored as candidate_correction) | exclude; practice focus
Room   ── visible transcript (turn_end.text), keys Esc/M/T//, mic retry, switch to typing
Results ◀─GET /api/sessions/<sid>/evaluation── per-round: queued→running→complete|failed|not_assessed
          finished rounds readable immediately; report only when all settle; retry failed rounds only
        ◀─GET …/report── report.v3 + disputes + revisions + contested (stored report never rewritten)
        ──POST …/findings/<fid>/dispute · /revision · /dispute/status
        ──POST /api/practice{session_id, finding_id}──▶ practice/<pid>/practice.json
Practice ── checklist (only the candidate's own words) ──WS session_start{practice_id}
          one round, short practice pack, same rubric ──▶ evaluation ──▶ before/after on one dimension
Delete  ── close live session → cancel+await tasks → remove everything; retention sweep uses the same routine
```

## Components

| Concern | Module | Notes |
|---|---|---|
| Identity & isolation | `src/interview/candidates.py` | Guest bearer token `<cid>.<secret>`; only SHA-256 of the secret stored; constant-time compare. Every path is built from the authenticated id, so another candidate's id resolves to nothing (404). |
| Upload validation | `intake/documents.py` | One list shared with the browser: `.pdf .docx .txt .md`, 5 MB. Content must match the name (PDF magic, DOCX zip with `word/document.xml`, no binary in text). DOCX read with the stdlib; inflated size capped. Scans/images refused with a recovery message. |
| Intake service | `services/intake.py` | Background job, 120 s timeout, states `queued → reading_documents → indexing_repository → building_claims → preparing_guidance → ready | failed`. A repository failure is a warning, never an intake failure. |
| Repository | `intake/repo_indexer.py` | https URLs on GitHub/GitLab/Bitbucket/Codeberg only, no credentials. `git clone --depth 1 --no-tags`, no prompts, no symlinks, no file transport, 25 s timeout, 20 MB cap. Secrets (`.env*`, keys, credentials, db files) and generated dirs (`node_modules`, `dist`, `vendor`, …) are never listed, read or grepped. Nothing is executed. The clone is deleted after indexing. |
| Claims | `intake/claims.py` | ≤ 8 claims, each a verbatim span of something read. `evidence_kind`: `candidate_assertion` (resume/background), `repository`, `work_sample`. Tagged with the round competency it is probed under. Repository content shows material exists, not who wrote it. |
| Interview config | `session/interview_config.py` | One validated model (role, family, seniority, round, lane, difficulty, background, JD, company, repo). Persisted with the intake. |
| Rounds | `session/coordinator.py`, `session/roles.py`, `session/runtime.py` | Rounds mode runs whenever a session starts from an intake. Full = HR → Hiring manager → Domain specialist; a single round runs alone. Each round has its own pack spine, time slice, depth cap, rubric, proposer. One round active ⇒ one voice. Handoff brief carries quoted statements + open questions, never ratings. Questions already asked anywhere count as asked for the guard's repeat rule. |
| Question choice | `session/proposer.py`, `session/agent.py`, `session/guard.py` | With Groq: `ModelProposer` (one bounded JSON call for the active round; data delimited). Without: `DeterministicProposer` (role-aware, labelled "development interviewer"). The guard still rules: spine verbatim and in order, time budget, depth cap, claims/transcript scope, no repeats. A model failure falls back to the deterministic proposer **and tells the candidate** (`provider_warning`). |
| Progress | `LiveSession.progress_payload` | Server-reported per round: core (spine) questions asked/total, follow-ups, answers, round i of n. No client-side totals. |
| Voice | `transport/voice_session.py`, `deepgram_stt.py`, `deepgram_tts.py` | Browser PCM16 → server resample → Deepgram nova-3; Aura-2 TTS with metadata-framed audio; barge-in clears provider + browser queue; truncation at last acknowledged word. Voice failure → `voice_error` + explicit "Continue by typing" (`switch_to_text`). Production refuses voice without a provider instead of mock audio. |
| Lifecycle & evaluation job | `services/evaluation.py` | Idempotent enqueue after close-out; explicit retry; "evaluating" with no task after a restart is reported failed-interrupted. Never substitutes a fixture. |
| Evaluation | `evaluation/role_eval.py` | One perspective per round that ran (HR / hiring manager / domain specialist) against that pack's rubric, concurrently. JSON contract validated; up to 3 attempts with the validation error fed back; then the pass fails explicitly. Every citation/finding quote verified against the named candidate turn; unverified evidence discarded and counted; a dimension left without verified evidence becomes `insufficient_evidence` (no score). Claim `held`/`collapsed` require a verified quote; disagreements between perspectives are reported with both positions, final status `untested`. |
| Scores | `evaluation/rubrics.py` | Round score = weighted mean over **assessed** dimensions only, weights renormalised and shown. Overall = rounds' scores weighted by round share, renormalised over scored rounds. No neutral 0.5 anywhere: missing evidence is "Not scored". |
| Delivery | `role_eval.delivery_observations` | Voice only. Measured: answer length, words/minute from STT word timings, against the candidate's own first answer. Never in any score. Not measured: tone, pitch, accent, appearance. Text lane: "not assessed". |
| Store & history | `roadmap/reports.py`, `services/history.py` | `session_reports`, `round_dimension_scores`, `report_gaps`. Comparable = same profession, round selection, lane, rubric versions, evaluator kind. 1 session → no comparison; 2 → "change since previous comparable session"; ≥3 → trend. Difficulty differences are stated. |
| **(16)** Settings | `src/interview/config.py` | One source: process env > `.env` > defaults. `inference.yaml` keeps only generation defaults and TTS vendor. `interviewer_mode` / `evaluator_mode` (`groq` / `deterministic`/`mock` / `unavailable`); `MOCK_LLM` requires mocks allowed; prod refuses mocks. |
| **(16)** Model client | `src/interview/llm/client.py` | SDK retries off; per-request timeout; `GROQ_MAX_RETRIES` retries for 429/408/409/5xx/timeout/connection honouring `Retry-After`; one fallback hop (never on auth); optional overall deadline; returns provider/model_used/fallback/attempts/limiter wait/request time/tokens. Live proposer uses `LIVE_MODEL_DEADLINE_S` and the per-turn budget (keyed by turn id). |
| **(16)** Diagnostics | `services/diagnostics.py`, `tools/check_providers.py` | Configured vs authenticated-request-succeeded; per-model availability; cached; no secrets. |
| **(16)** Evaluation jobs | `services/evaluation.py` | Per-round states and persisted results (`evaluation/rounds/<round>.json`), concurrent rounds, failed-round retry, cache keyed by candidate/session/transcript/claims/rubric/prompt/evaluator, restart → `interrupted`, deadlines, timings (end → first result → report). Writes refuse to recreate deleted candidates. |
| **(16)** Feedback provenance | `evaluation/role_eval.py` | report.v3: `source_match` (≥ 12 chars, correct turn of that round), question + limitation + action per finding, top-3 priority, plain claim labels, `insufficient_evidence` rounds with no model call. Repairs only for invalid/truncated JSON. |
| **(16)** Disputes | `services/feedback.py` | Disputes and labelled re-checks stored beside the report; excluded from comparisons, recurring gaps, plan and practice while open. |
| **(16)** Practice | `services/practice.py` | Server-derived record, checklist from candidate-supplied facts only, practice pack through the coordinator (`pack_overrides`), same-rubric comparison with explicit outcomes, unaided variation. Practice reports stored with `session_kind='practice'` and never on trends. |
| **(16)** Intake review | `services/intake.py` | Source spans, keep/edit/exclude (`review.json`; `claims.json` untouched), practice focus passed to proposers as data (guard unchanged), consent record, write guards for deletion. |
| **(16)** Retention | `services/retention.py` | Hourly sweep of guests inactive for `GUEST_RETENTION_DAYS`, using the delete routine. |
| Results UI | `client/src/pages/ResultsDashboard.tsx`, `PracticePage.tsx` | **(16)** Polls `/evaluation` for per-round states with real elapsed seconds (no percentage); shows finished rounds early; retries failed rounds only. Report leads with ≤ 3 practice findings (question, quoted answer "found in your answer", why, limits, next step), then dimension levels with the answer behind each, then the overall indicator. Dispute / re-check / accept / withdraw; "Practise this gap"; practice page with checklist, attempts and before/after. History and Next practice list practice; delete explains what it cannot reach. |

Contracts preserved: typed events (new additive `round_transition`, additive
`session_complete.rounds/ended_reason`, `fallback_used.kind=voice_to_text`),
append-only JSONL per session, live path imports nothing from evaluation (the
composition root queues the job through `services/` after close-out), repo and
resume text only reach models as delimited data.

## Configuration

`.env` (copy `.env.example`; never commit). The variables that matter:

| Variable | Purpose |
|---|---|
| `APP_ENV` | `dev` / `test` / `prod`. `prod` refuses mocks, sessions without an intake, and voice without Deepgram. |
| `ALLOW_MOCK_PROVIDERS` | `1` in dev/test only. Must be `0` with `APP_ENV=prod` (startup refuses otherwise). |
| `GROQ_API_KEY` | Interviewer and evaluator. Without it (dev): deterministic interviewer + labelled mock evaluator. |
| `MODEL_LIVE_INTERVIEWER`, `MODEL_EVALUATOR`, `MODEL_FALLBACK_FAST`, `MODEL_FALLBACK_QUALITY` | Groq model ids. Set fallbacks to a *different* model than the primary or failover is a no-op. |
| `DEEPGRAM_API_KEY` | Real STT + TTS. |
| `DATA_DIR` | Candidate data + `reports.sqlite`. Default `data/` (git-ignored). |
| `MOCK_LLM` | `1` forces the deterministic interviewer and mock evaluator (tests/dev). |
| `MAX_LIVE_SESSIONS`, `INTAKE_RPH`, `RESUME_TTL_S` | Session cap, intakes per candidate per hour, reconnect window. |
| `VITE_WS_HOST` (client) | Backend `host:port`; default `<page host>:8000`. |

## Run

Exact Windows PowerShell steps are in the root `README.md`. In short:

```powershell
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m uvicorn interview.server:app --host 127.0.0.1 --port 8000
# second terminal
cd client; npm install; npm run dev     # http://localhost:5173
```

## Verify

```bash
python -m pytest -q                                    # offline suite, deterministic fakes
python -m pytest -q tests/test_stage15_journey.py      # the journey A–I through the real API
cd client && npx tsc --noEmit && npx vite build
python tools/live_journey_smoke.py --out logs/live_journey   # REAL Groq: intake→interview→evaluation→report
python tools/live_voice_smoke.py --yes                       # REAL Deepgram TTS/STT loop + Groq follow-up + barge-in
```

The two `tools/live_*` scripts make billable provider calls; the test suite
never does (`tests/conftest.py` blanks provider keys from `.env`).

## Demo sequence (uses real session results)

1. Start backend and client with `GROQ_API_KEY` set; `/health` shows
   `interviewer: groq, evaluator: groq` (and `voice: deepgram` if set).
2. Setup: upload a resume (PDF/DOCX/TXT), target role, profession "Sales" or
   "Software", round "Full interview", answer mode, difficulty, optional JD.
3. Watch real intake stages; on the review screen point out each claim and its
   evidence kind, the JD terms not mentioned, and the "no company research"
   item when no company context was given.
4. Start. Show the round stepper and "core question x of y · n follow-ups";
   answer two or three questions; show the spoken/visible handover and the
   "carried forward: statements and open questions — no ratings" notice.
5. End early (or finish). Results page shows evaluating → complete in roughly
   15 s per round on Groq. Walk through: rubric weights and levels with the
   quoted turn behind each, rounds not reached marked "Not scored", claim
   findings with their meanings, limitations ("you ended early…").
6. Download the transcript and the scorecard (two different files).
7. Run a second session with the same setup; History shows "change since your
   previous comparable session" with the caveat, not a trend.
8. Delete my data → manifest; the history is gone and the token is revoked.

Evidence from a real run is in `docs/decisions/stage15_journey.md`.

## Not implemented (do not demo as features)

- **E2B / live code execution / console access.** `docs/decisions/stage11_live_coding_proposal.md`
  is a proposal only; no sandbox exists and repository code is never executed.
- **Simultaneous panel voices.** Legacy `PANEL_MODE` packs still exist for the
  old single-pack path but browser sessions run sequential rounds with one voice.
  "Hard" difficulty is a follow-up depth setting, not a panel.
- **Per-round TTS voices.** Rounds are labelled on screen and in the transcript,
  but Deepgram synthesis uses one configured voice for the whole session.
- **Company research.** Only company context the candidate types is used, and it
  is labelled as such.
- **Accounts.** Guest tokens only: one browser, no recovery, bearer secret.
- **Employer screening, ranking, hiring decisions.** None, by design.
